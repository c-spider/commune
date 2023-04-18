import os, sys
from pprint import pp

from functools import partial
import asyncio
from copy import deepcopy
from typing import Union, Optional, List
from concurrent import futures
import os, sys
from typing import *
from loguru import logger
import time
from munch import Munch
import argparse
import torch
import json

import streamlit as st


# logger = logger.opt(colors=True)
    
# import torch
import commune
from commune.model import Model
# commune.utils
from torch import nn
# commune.new_event_loop()
from commune.metric import MetricMap

from commune.utils.tokenizer import  decode_topk, get_translation_map, encode_topk, prep_tokenizer
 
"""
Examples 



"""
class TransformerModel( Model):
    shortcuts =  {
        # 0-1B models
        'gpt125m': 'EleutherAI/gpt-neo-125m',

        # 1-3B models
        'gpt2.7b': 'EleutherAI/gpt-neo-2.7B',
        'gpt3b': 'EleutherAI/gpt-neo-2.7B',
        'opt1.3b': 'facebook/opt-1.3b',
        'opt2.7b': 'facebook/opt-2.7b',

        # 0-7B models
        'gptjt': 'togethercomputer/GPT-JT-6B-v1',
        'gptjt_mod': 'togethercomputer/GPT-JT-Moderation-6B',
        'gptj': 'EleutherAI/gpt-j-6b',
        'gptj.pyg6b': 'PygmalionAI/pygmalion-6b',
        'gpt6b': 'cerebras/Cerebras-GPT-6.7B',
        'gptj.instruct': 'nlpcloud/instruct-gpt-j-fp16',
        'gptj.codegen': 'moyix/codegen-2B-mono-gptj',
        'gptj.hivemind': 'hivemind/gpt-j-6B-8bit',
        'gptj.adventure': 'KoboldAI/GPT-J-6B-Adventure',
        'gptj.pygppo': 'TehVenom/GPT-J-Pyg_PPO-6B', 
        'gptj.alpaca.gpt4': 'vicgalle/gpt-j-6B-alpaca-gpt4',
        'gptj.alpaca': 'bertin-project/bertin-gpt-j-6B-alpaca',
        'oa.galactia.6.7b': 'OpenAssistant/galactica-6.7b-finetuned',
        'opt6.7b': 'facebook/opt-6.7b',
        'llama': 'decapoda-research/llama-7b-hf',
        'vicuna.13b': 'lmsys/vicuna-13b-delta-v0',
        'vicuna.7b': 'lmsys/vicuna-7b-delta-v0',
        'llama-trl': 'trl-lib/llama-7b-se-rl-peft',
        'opt.nerybus': 'KoboldAI/OPT-6.7B-Nerybus-Mix',

        # # > 7B models
        'oa.pythia.12b': 'OpenAssistant/oasst-sft-1-pythia-12b',
        'gptneox': 'EleutherAI/gpt-neox-20b',
        'gpt20b': 'EleutherAI/gpt-neox-20b',
        'opt13b': 'facebook/opt-13b',
        'gpt13b': 'cerebras/Cerebras-GPT-13B'
        
         }
    

    def __init__(self, model = 'gp125m',
                **kwargs
                ):
        
        Model.__init__(self, locals())         
        config = self.config
        self.set_model(config)    
        
        if config.test:
            self.test(self)

    default_tag = 'base'
    def set_tag(self,tag:str):
        if tag is None:
            tag = self.default_tag
        self.tag = tag
    @classmethod
    def calculate_loss( cls,  **kwargs) -> torch.Tensor:
        '''
        Calculate the loss for the model.
        '''
        pred = kwargs['logits']
        gt = kwargs['input_ids'][:, -(pred.shape[1]-1):].flatten()
        return_value = kwargs.get('return_value', False)
        pred = pred[:, :pred.shape[1]-1]
            
        if len(pred.shape) == 3:
            pred = pred.reshape(-1, pred.shape[-1])
        
        assert gt.shape == pred.shape[:1], f'gt.shape: {gt.shape} pred.shape: {pred.shape}'

        loss_fn = torch.nn.CrossEntropyLoss()
        loss =  loss_fn(pred, gt.to(pred.device))
        if return_value:
            return loss.item()
        return loss

    def _forward(self,  
                input_ids: torch.Tensor, 
                topk:int=32,
                output_length:int = 10,
                output_hidden_states : bool = True,
                hidden_state_index: int = -1,
                hidden_dim_bounds: List =  [0, -1],
                return_keys:List[str] = ['topk', 'stats'],
                train: bool = False,   
                map_tokens: bool = True,
                map_logits: bool = False,                             
                **kwargs):

        sample = {
        'input_ids': input_ids,
        }
    
        if map_tokens:
            
            sample['input_ids'] = self.token_translator.translate_tokens(sample['input_ids'])
        
        for k,v in sample.items():
            if isinstance(v, torch.Tensor):
                sample[k] = sample[k].to(self.device)
        

            
        # clip the input ids to the vocab size
        sample['input_ids'] = torch.clip(sample['input_ids'], 0, self.tokenizer.vocab_size-1)
        if train:
            self.optimizer.zero_grad()
            
        device = self.get_model_device(self.model)
        
        self.stats['time'] =  self.time()
        sample['input_ids'] = sample['input_ids'].to(device)
        model_output = self.model(input_ids=sample['input_ids'].to(device),
                                  output_hidden_states=output_hidden_states)
        self.stats['latency'] = self.round(self.time() - self.stats['time'], sig=2)
        
        self.stats['inference_steps'] = self.stats.get('inference_steps', 0) + 1
        # sometime we dont care about the begginning of the sequence
        
        output_length = output_length if output_length else model_output.logits.size(1)
        
        output_dict = {}
        # logits
        output_dict['logits']= model_output.logits[:,-output_length:,:]
        
        if map_logits:
            output_dict['logits'] = self.token_translator.translate_logits(output_dict['logits'])
        # topk
        output_dict['topk']=self.encode_topk(output_dict['logits'], topk=topk)
        
        # hidden state
        output_dict['hidden_states'] = model_output.hidden_states[hidden_state_index]
        output_dict['hidden_states'] = output_dict['hidden_states'][:,-output_length:,:]
        output_dict['hidden_states'] = output_dict['hidden_states'][:, :, hidden_dim_bounds[0]:hidden_dim_bounds[1]]
        
        output_dict['input_ids'] = sample['input_ids']
        loss = self.calculate_loss(**output_dict) 
        
        if train:
            loss.backward()
            self.optimizer.step()
            self.stats['learn_steps'] = self.stats.get('learn_steps', 0) + 1
            self.stats['learn_rate'] = self.optimizer.param_groups[0]['lr']
        if isinstance(loss, torch.Tensor):
            loss = loss.item()
        
        inference_steps = self.stats['inference_steps']
        past_loss = self.stats.get('loss', 0)
        self.stats['loss'] = (past_loss*(inference_steps-1) + loss ) / inference_steps
        output_dict['stats'] = deepcopy(self.stats)
        output_dict['stats']['sample_loss'] = loss  

        return {key:output_dict[key] for key in return_keys} 
        

        
        
    def set_model(self, config) -> None:
        
        
        from transformers import  AutoModelForCausalLM, AutoModel, AutoConfig
        from accelerate import init_empty_weights

        model_name = config['model_name'] = config['model']
        self.model_path = config['model_path'] =self.shortcuts.get(model_name, model_name)
        # config = AutoConfig.from_pretrained(self.model_name)
        
        print(f'loading config model from {self.model_path}...')
        model_config = AutoConfig.from_pretrained(self.model_path)
        model_config_dict = model_config.to_dict()
        for k,v in model_config_dict.items():
            assert k not in config, f'config key {k} not found in config'
            config[k] = model_config_dict[k]        
        config = self.munch(config)
        
        with init_empty_weights():
            self.model = AutoModelForCausalLM.from_config(model_config)
    
        model_size = self.get_model_size(self.model)
        
                        
        free_gpu_memory = self.free_gpu_memory(fmt='b', 
                                          max_allocation_ratio=config['max_allocation_ratio'])
        gpus = list(free_gpu_memory.keys()) 
        total_gpu_memory = sum(free_gpu_memory.values())
        
        
        assert model_size < total_gpu_memory, f'model size {model_size} is larger than total gpu memory {total_gpu_memory}'


        self.print(f'{model_size}')

        
        unallocated_model_memory = model_size
        
        max_memory = {k:0 for k in gpus}
        
        
        buffer_memory_factor = 1.1
        while unallocated_model_memory > 0:
            most_free_gpu, most_free_gpu_memory = self.most_free_gpu(free_gpu_memory=free_gpu_memory, return_tuple=True)

            
            allocated_memory = min(unallocated_model_memory, most_free_gpu_memory)
            unallocated_model_memory -= allocated_memory
            reserved_memory = allocated_memory * buffer_memory_factor
            reserved_memory  = min(reserved_memory, most_free_gpu_memory)
            max_memory[most_free_gpu] = reserved_memory
            free_gpu_memory[most_free_gpu] -= max_memory[most_free_gpu]
            
            
        for k,v in max_memory.items():
            max_memory[k] = f'{int(v//1e9)}GB'
            

            
        self.print(f'max memory: {max_memory}')

        model_kwargs = {'max_memory': max_memory}
        for k in ['load_in_8bit', 'device_map']:
            if k in config:
                model_kwargs[k] = config[k]
                

        self.model = AutoModelForCausalLM.from_pretrained(self.model_path, **model_kwargs) 
        
        self.device = self.model.device 
    
        
        self.set_tokenizer(config)
        self.set_optimizer(config.optimizer)
        self.set_finetune(config.finetune)   
        self.set_tag(config.tag)
        self.set_stats(config.stats)    
        self.set_epoch_length(config.epoch_length)        
        if config.load:
            self.load() 
            
        self.config = config


    def set_epoch_length(self, epoch_length:int) -> int:
        assert isinstance(epoch_length, int)
        self.epoch_length = epoch_length
        return self.epoch_length

    def set_tokenizer(self, config):
        from transformers import AutoTokenizer, AutoModel
        from commune.utils.tokenizer import prep_tokenizer

        
        if config.tokenizer is None:
            tokenizer = config.model_path
        assert isinstance(tokenizer, str, )
        tokenizer = self.shortcuts.get(tokenizer, tokenizer)
        self.config['tokenizer'] = tokenizer
        
        try:
            # HACK TO INCLUDE LLAMA TOKENIZER
            tokenizer = AutoTokenizer.from_pretrained(tokenizer, use_fast= True)
        except ValueError:
            
            print('resorting ot use_fast = False')
            tokenizer = AutoTokenizer.from_pretrained(tokenizer, use_fast=False)


        self.tokenizer = tokenizer
        
    
        self.std_tokenizer = AutoTokenizer.from_pretrained('gpt2', use_fast= True)
        self.std_tokenizer = prep_tokenizer(self.std_tokenizer)
        self.tokenizer = prep_tokenizer(self.tokenizer, self.std_tokenizer)
        self.token_translator = self.get_module('model.token_translator')(tokenizer=tokenizer, std_tokenizer=self.std_tokenizer)

        return self.tokenizer

    
    
    @staticmethod
    def encode_topk( forward_response_tensor: torch.Tensor , topk:int=4096) -> torch.Tensor:
        """ Returns topk tokens/probabilities given unnormalized logits as input. """

        #import ipdb; ipdb.set_trace()

        logits = forward_response_tensor  # unnormalized logit scores: [batch_size, sequence_len, vocab_size]
        probs = torch.softmax(logits, dim=-1).to(torch.float32)  # normalized probabilities: [batch_size, sequence_len, vocab_size]

        topk_indices = torch.argsort(probs, dim=-1, descending=True)[...,:topk]
        # topk_values, topk_indices = torch.topk(probs, topk) # topk probs and indices: [batch_size, sequence_len, topk]

        topk_values = probs.gather( index=topk_indices, dim=-1)
        encoded_probs = torch.cat([topk_values, topk_indices], dim=-1)  # [batch_size, sequence_len, topk + topk]
        return encoded_probs  # [batch_size, sequence_len, topk + topk]


    def tokenizer_name(self):
        return self.config['tokenizer']

    def tokenize(self, text: str = 'Whadup',
                 padding=True, 
                 truncation=True, 
                 max_length=64,
                 return_tensors='pt',
                 add_special_tokens=False,
                 device:str = None, 
                 **kwargs) -> torch.Tensor:
        """ Returns tokenized text as torch tensor. """
        
        sample = self.tokenizer(text, 
                                             padding=padding, 
                                             truncation=truncation, 
                                             max_length=max_length, 
                                             return_tensors=return_tensors,
                                             add_special_tokens=add_special_tokens, 
                                             **kwargs)  # assume tokenizer.padding_side = 'left'

        device = device if device != None else self.device
        
        sample = dict(
            input_ids= sample['input_ids'].to(device),
            attention_mask= sample['attention_mask'].to(device)
        )
        
        return sample



    def detokenize(self, input_ids: torch.Tensor, **kwargs) -> torch.Tensor:
        """ Returns tokenized text as torch tensor. """
        
        text = self.tokenizer.batch_decode(input_ids,**kwargs)  # assume tokenizer.padding_side = 'left'

        return text


    @classmethod
    def test(cls, model = 'opt1.3b', 
             topk:int=256 ,
             dataset:str = 'dataset.text.bittensor',
             num_batches = 100,
             sequence_length = 256,
             batch_size = 32,
             minimum_loss = 4, 
             lr = 1e-4,
             remote = False, 
             load = False,
             ):
        
        
        

        if remote and model in namfespace:
            namespace = cls.namespace()
            model_name = f'model.{model}'
            model = cls.connect(model_name)
        
        elif isinstance(model, str):
            model = cls(model= model, load=load, optimizer=dict(lr=lr))
        else:
            model = model
        
        

        dataset = commune.connect(dataset)

        for i in range(num_batches):
            sample = dataset.sample(batch_size=batch_size,sequence_length=sequence_length, no_tokenizer=False)
            sample['topk'] = topk
            sample['map_tokens'] = True
            sample['map_logits'] = False
            sample['timeout'] = 6
            output = model.forward(**sample)
            cls.print(output)
            cls.print(output['stats'])
        
        # print(cls.calculate_loss(output['logits'].reshape(-1, output['logits'].shape[-1]), targets[:, -output_length:].flatten()))
        

    @classmethod
    def run_train(cls,
              model:str='gptj', 
              dataset : Union[str, 'Module'] = 'dataset::bittensor',
             output_length:int=10,
             sequence_length:int=256,
             adapter: dict = None,
             num_batches: int = 10000, 
             tag:str=None,
             load: bool = False,
             save: bool= True,
             refresh: bool = False):
        if refresh:
            load = False
            

        model = cls(model=model, tag=tag, load=load)
        
        if isinstance(dataset, str):
            dataset = commune.connect(dataset)

        for i in range(num_batches):
            sample = dataset.sample(sequence_length=sequence_length)
            sample['output_length'] =  output_length
            sample['return_keys'] = ['stats']
            sample['train'] = True
            output = model.forward(**sample)
            print(output)
        if save:
            model.save(tag=tag)
            
        return output['stats']
    
    
    def train_model(self,
             dataset : Union[str, 'Module'] = 'dataset::bittensor',
             params: dict = None,
            output_length:int=10,
            sequence_length:int=256,
            num_batches: int = 1, 
            tag : str = None,
            save : bool = False,
            load : bool = False,
            refresh: bool = False,
            **kwargs):
        st.write(self.config)

        params = params if params != None else {}
        params['tag'] = tag

        if load and (refresh == False):
            self.load(tag=tag)
        
        self.set_params(**params)
        
        if not hasattr(self, 'dataset'):
            if isinstance(dataset, str):
                dataset = commune.connect(dataset)
            self.dataset = dataset
            
            
            
        for i in range(num_batches):
            sample = self.dataset.sample(sequence_length=sequence_length)
            if isinstance(sample, str):
                continue
            sample.update(dict(
                output_length=output_length,
                return_keys=['stats'],
                train = True
            ))
            
            output = self.forward(**sample)
            commune.print(output, 'cyan')

        if save :
            self.save(tag=tag)
            
        return output['stats']

    
    @classmethod
    def models(cls):
        return list(cls.shortcuts.keys())
    
    
    @classmethod
    def remote_train(cls,
             model:str='model::gptj::5',  
             dataset : Union[str, 'Module'] = 'dataset::bittensor',
             params: dict = None,
            output_length:int=10,
            sequence_length:int=256,
            num_batches: int = 100, 
            num_epochs: int = 100,
            tag : str = None,
            save : bool = True,
            load : bool = False,
            refresh: bool = False,
            **kwargs):
        self = commune.connect(model)
        params = params if params != None else {}
        params['tag'] = tag

        if load and (refresh == False):
            self.load(tag=tag)
        
        self.set_params(**params)
        
  
        dataset = commune.connect(dataset)
            
        best_epoch_loss = self.stats.get('best_epoch_loss', 10)
        for epoch in range(num_epochs):
            epoch_loss = 0
            for batch_idx in range(num_batches):
                
                sample = dataset.sample(sequence_length=sequence_length)
                
                print(sample)
                if isinstance(sample, str):
                    continue
                
                sample.update(dict(
                    output_length=output_length,
                    return_keys=['stats'],
                    train = True
                ))
                
                output = self.forward(**sample)
                epoch_loss = output['stats']['loss'] / (batch_idx + 1)
                commune.print(output, 'cyan')
                
                
            if epoch_loss < best_epoch_loss and save:
                output['stats']['epoch_loss'] = epoch_loss
                output['stats']['num_batches'] = num_batches
                output['stats']['best_epoch_loss'] = best_epoch_loss
                self.set_stats(stats=dict(epoch=epoch, loss=epoch_loss))
                self.save(tag=tag)

        return output['stats']
    

    default_models = list(shortcuts.keys())
          
          
    fleet_group = {
        
        '0': [ 'gpt125m', 'gpt2.7b', 'opt2.7b','gptj'],
        '1': [ 'gptj.alpaca', 'gptj.pygppo', 'opt6.7b', 'oa.galactia.6.7b', 'vicuna.7b', 'gptj'],
        '2': [ 'gptj.instruct', 'gpt6b', 'opt6.7b', 'oa.galactia.6.7b', 'vicuna.7b', 'gptj'],


        # '0': ['vicuna.7b', 'opt6.7b', 'oa.galactia.6.7b'],

        'all': default_models,
        'default': default_models,
    }
    @classmethod
    def deploy_fleet(cls, 
                     models: List[str] = '0',
                     replace: bool = False,
                     max_models: int = -1,
                     wait_for_server = False
                     ) -> List[str]:


        
        models = cls.fleet_group.get(models, models)
    
    
        deployed_model_tags = {}
        models = models
        deployed_models = []
        for model in models:
            commune.print(f'Deploying Model {model}', 'green')
            cls.deploy(model, wait_for_server=wait_for_server, replace=replace)
            deployed_models.append(model)
            commune.print(f'Deployed Model {model} ({len(deployed_models)}/{len(models)})', 'green')
            
            
        return deployed_models
        
    @classmethod
    def undeployed_models(cls, models: List[str] = 'all'):
        models = cls.fleet_group.get(models, models)
        undeployed_models = []
        for model in models:
            if cls.module_exists(f'model.{model}') == False:
                undeployed_models.append(model)
        return undeployed_models
        
    @classmethod
    def deploy(cls,
               *models: str,
               tokenizer: str=None, 
               name: str =None, 
               wait_for_server: bool = False, 
               mode:str = 'pm2',
               tag = None,
               replace:bool = False,
               **kwargs):


        assert len(models) > 0
        model_names = []
        for model in models:
            model_kwargs =  {'model': model, 'tokenizer': tokenizer, **kwargs}
            name = f'model.{model}'
            if tag != None:
                name = f'{name}.{tag}'
            module_exists = cls.module_exists(name)     
            if replace == False and module_exists:
                cls.print(f'Model {name} already exists', color='yellow')
                continue
            cls.launch(name=name,kwargs=model_kwargs, mode=mode)
            if wait_for_server:
                cls.wait_for_server(name=name, sleep_interval=20, timeout=1000)
            model_names.append(name) 
        return model_names
            
    @classmethod
    def sandbox(cls):
        self = cls(model='opt2.7b')
        
        
if __name__ == "__main__":
    
    TransformerModel.run()

