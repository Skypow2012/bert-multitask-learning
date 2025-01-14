# AUTOGENERATED! DO NOT EDIT! File to edit: source_nbs/11_modeling.ipynb (unless otherwise specified).


from __future__ import absolute_import, division, print_function


__all__ = ['MultiModalBertModel', 'LOGGER']

# Cell
#nbdev_comment from __future__ import absolute_import, division, print_function

import collections
import re

import six
import tensorflow as tf
import transformers

from .params import BaseParams

from .utils import (get_embedding_table_from_model,
                    load_transformer_model, get_shape_list)

LOGGER = tf.get_logger()


class MultiModalBertModel(tf.keras.Model):
    def __init__(self, params: BaseParams, use_one_hot_embeddings=False):
        super(MultiModalBertModel, self).__init__()
        self.params = params
        if self.params.init_weight_from_huggingface:
            self.bert_model = load_transformer_model(
                self.params.transformer_model_name)
        else:
            self.bert_model = load_transformer_model(self.params.bert_config)
            self.bert_model(tf.convert_to_tensor(
                transformers.file_utils.DUMMY_INPUTS))
        self.use_one_hot_embeddings = use_one_hot_embeddings

        # multimodal input dense
        embedding_dim = get_embedding_table_from_model(
            self.bert_model).shape[-1]
        self.modal_name_list = ['image', 'others']
        self.multimodal_dense = {modal_name: tf.keras.layers.Dense(
            embedding_dim) for modal_name in self.modal_name_list}

        # multimodal modal type embedding
        # this might raise no gradients warning if it's unimodal
        # variable: [3, 768]
        if self.params.enable_modal_type:
            self.modal_type_embedding = tf.keras.layers.Embedding(input_dim=len(
                self.modal_name_list)+1, output_dim=embedding_dim)

        self.enable_modal_type = self.params.enable_modal_type

    def call(self, inputs, training):
        features_dict = inputs
        input_ids = features_dict['input_ids']
        input_mask = features_dict['input_mask']
        token_type_ids = features_dict['segment_ids']
        input_shape = get_shape_list(input_ids)
        batch_size = input_shape[0]
        seq_length = input_shape[1]

        if input_mask is None:
            input_mask = tf.ones(
                shape=[batch_size, seq_length], dtype=tf.int32)

        if token_type_ids is None:
            token_type_ids = tf.zeros(
                shape=[batch_size, seq_length], dtype=tf.int32)

        config = self.bert_model.config

        self.embedding_output = tf.gather(
            get_embedding_table_from_model(self.bert_model), input_ids)

        # we need to add [SEP] embeddings around modal input
        # Since the last input_ids is always [SEP], we can use it directly
        sep_embedding = tf.expand_dims(
            self.embedding_output[:, -1, :], axis=1)

        if self.enable_modal_type:
            # for multimodal
            modal_type_ids = tf.zeros_like(input_ids)
        else:
            modal_type_ids = None

        for modal_name in self.modal_name_list:
            input_name = '{}_input'.format(modal_name)
            segment_id_name = '{}_segment_ids'.format(modal_name)
            mask_name = '{}_mask'.format(modal_name)
            if input_name not in features_dict:
                continue

            if not self.enable_modal_type:
                LOGGER.warning('Seems there\'s a multimodal inputs but params.enable_modal_type is '
                               'not set to be True.')

            # convert other modal embeddings to hidden_size
            # [batch_size, seq_length, modal_dim] -> [batch_size, seq_length, hidden_size]
            modal_input = self.multimodal_dense[modal_name](
                features_dict[input_name])

            # add sep embedding
            modal_input = tf.concat(  # pylint: disable=unexpected-keyword-arg,no-value-for-parameter
                [sep_embedding, modal_input, sep_embedding], axis=1)
            # add same type id to left and right
            modal_segment_ids = tf.concat(  # pylint: disable=unexpected-keyword-arg,no-value-for-parameter
                [tf.expand_dims(features_dict[segment_id_name][:, 0], axis=1),
                    features_dict[segment_id_name],
                    tf.expand_dims(features_dict[segment_id_name][:, 0], axis=1)], axis=1)
            # add mask
            modal_mask = tf.concat(  # pylint: disable=unexpected-keyword-arg,no-value-for-parameter
                [tf.expand_dims(features_dict[mask_name][:, 0], axis=1),
                    features_dict[mask_name],
                    tf.expand_dims(features_dict[mask_name][:, 0], axis=1)], axis=1)
            # add modal type
            if self.enable_modal_type:
                this_modal_type_ids = tf.ones_like(
                    modal_segment_ids) * self.params.modal_type_id[modal_name]

            # concat to text correspondingly
            self.embedding_output = tf.concat(  # pylint: disable=unexpected-keyword-arg,no-value-for-parameter
                [self.embedding_output, modal_input], axis=1)
            token_type_ids = tf.concat(  # pylint: disable=unexpected-keyword-arg,no-value-for-parameter
                [token_type_ids, modal_segment_ids], axis=1)
            input_mask = tf.concat(  # pylint: disable=unexpected-keyword-arg,no-value-for-parameter
                [input_mask, modal_mask], axis=1)
            if self.enable_modal_type:
                modal_type_ids = tf.concat(
                    [modal_type_ids, this_modal_type_ids], axis=1)

        self.model_input_mask = input_mask
        self.model_token_type_ids = token_type_ids
        if self.enable_modal_type:
            self.model_modal_type_ids = modal_type_ids

        word_embedding = self.embedding_output
        if self.enable_modal_type:
            word_embedding = word_embedding + \
                self.modal_type_embedding(modal_type_ids)

        outputs = self.bert_model(
            {'input_ids': None,
             'inputs_embeds': word_embedding,
             'attention_mask': input_mask,
             'token_type_ids': token_type_ids,
             'position_ids': input_mask},
            training=training,
            output_hidden_states=True,
            return_dict=True
        )
        self.sequence_output = outputs.last_hidden_state
        if 'pooler_output' in outputs:
            self.pooled_output = outputs.pooler_output
        else:
            # no pooled output, use mean of token embedding
            self.pooled_output = tf.reduce_mean(outputs.last_hidden_state, axis=1)
        self.all_encoder_layers = tf.stack(outputs.hidden_states, axis=1)
        return outputs

    def get_pooled_output(self):
        return self.pooled_output

    def get_sequence_output(self):
        """Gets final hidden layer of encoder.

        Returns:
          float Tensor of shape [batch_size, seq_length, hidden_size] corresponding
          to the final hidden of the transformer encoder.
        """
        return self.sequence_output

    def get_all_encoder_layers(self):
        return self.all_encoder_layers

    def get_embedding_output(self):
        """Gets output of the embedding lookup (i.e., input to the transformer).

        Returns:
          float Tensor of shape [batch_size, seq_length, hidden_size] corresponding
          to the output of the embedding layer, after summing the word
          embeddings with the positional embeddings and the token type embeddings,
          then performing layer normalization. This is the input to the transformer.
        """
        return self.embedding_output

    def get_embedding_table(self):
        return get_embedding_table_from_model(self.bert_model)

    def get_input_mask(self):
        return self.model_input_mask

    def get_token_type_ids(self):
        return self.model_token_type_ids

