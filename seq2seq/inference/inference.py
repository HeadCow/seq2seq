# Copyright 2017 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

""" Generates model predictions.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from pydoc import locate

import numpy as np
import tensorflow as tf
from tensorflow.python.platform import gfile

from seq2seq import models
from seq2seq.data import input_pipeline, vocab
from seq2seq.training import utils as training_utils

def load_model(model_dir, mode, params=None):
  """Loads a model class from a given directory
  """

  train_options = training_utils.TrainOptions.load(model_dir)

  # Load vocabulary
  source_vocab_info = vocab.get_vocab_info(train_options.source_vocab_path)
  target_vocab_info = vocab.get_vocab_info(train_options.target_vocab_path)

  # Find model class
  model_class = locate(train_options.model_class) or \
    getattr(models, train_options.model_class)

  # Parse parameter and merge with defaults
  hparams = model_class.default_params()
  hparams.update(train_options.hparams)

  if params is not None:
    hparams.update(params)

  # Create model instance
  model = model_class(
      source_vocab_info=source_vocab_info,
      target_vocab_info=target_vocab_info,
      params=hparams,
      mode=mode)

  return model


def create_predictions_iter(predictions_dict, sess):
  """Runs prediciton fetches in a sessions and flattens batches as needed to
  return an iterator of predictions. Yield elements until an
  OutOfRangeError for the feeder queues occurs.

  Args:
    predictions_dict: The dictionary to be fetched. This will be passed
      to `session.run`. The first dimensions of each element in this
      dictionary is assumed to be the batch size.
    sess: The Session to use.

  Returns:
    An iterator of the same shape as predictions_dict, but with one
    element at a time and the batch dimension removed.
  """
  with tf.contrib.slim.queues.QueueRunners(sess):
    while True:
      try:
        predictions_ = sess.run(predictions_dict)
        batch_length = list(predictions_.values())[0].shape[0]
        for i in range(batch_length):
          yield {key: value[i] for key, value in predictions_.items()}
      except tf.errors.OutOfRangeError:
        break

def create_inference_graph(
    model_dir,
    input_file,
    batch_size=32,
    input_pipeline_def=None,
    params_overrides=None):
  """Creates a graph to perform inference.

  Args:
    model_dir: The output directory passed during training. This
      directory must contain model checkpoints.
    input_file: A source input file to read from.
    batch_size: The batch size used for inference
    beam_width: The beam width for beam search. If None,
      no beam search is used.

  Returns:
    The return value of the model functions, typically a tuple of
    (predictions, loss, train_op).
  """

  model = load_model(
      model_dir, tf.contrib.learn.ModeKeys.INFER, params_overrides)

  if model.params["inference.beam_search.beam_width"] > 1:
    tf.logging.info("Setting batch size to 1 for beam search.")
    batch_size = 1

  if input_pipeline_def is not None:
    pipeline = input_pipeline.make_input_pipeline_from_def(
        input_pipeline_def, shuffle=False, num_epochs=1)
  else:
    pipeline = input_pipeline.ParallelTextInputPipeline(
        source_files=[input_file],
        target_files=None,
        shuffle=False,
        num_epochs=1)

  input_fn = training_utils.create_input_fn(
      pipeline=pipeline,
      batch_size=batch_size,
      allow_smaller_final_batch=True)

  # Build the graph
  features, labels = input_fn()
  return model(
      features=features,
      labels=labels,
      params=None)


def unk_replace(source_tokens, predicted_tokens, attention_scores,
                mapping=None):
  """Replaces UNK tokens with tokens from the source or a
  provided mapping based on the attention scores.

  Args:
    source_tokens: A numpy array of strings.
    predicted_tokens: A numpy array of strings.
    attention_scores: A numeric numpy array
      of shape `[prediction_length, source_length]` that contains
      the attention scores.
    mapping: If not provided, an UNK token is replaced with the the
      source token that has the highest attention score. If provided
      the token is insead replaces with `mapping[chosen_source_token]`.

  Returns:
    A new `predicted_tokens` array.
  """
  result = []
  for token, scores in zip(predicted_tokens, attention_scores):
    if token == "UNK":
      max_score_index = np.argmax(scores)
      chosen_source_token = source_tokens[max_score_index]
      new_target = chosen_source_token
      if mapping is not None and chosen_source_token in mapping:
        new_target = mapping[chosen_source_token]
      result.append(new_target)
    else:
      result.append(token)
  return np.array(result)

def get_unk_mapping(filename):
  """Reads a file that specifies a mapping from source to target tokens.
  The file must contain lines of the form <source>\t<target>"

  Args:
    filename: path to the mapping file

  Returns:
    A dictinary that maps from source -> target tokens.
  """
  with gfile.GFile(filename, "r") as mapping_file:
    lines = mapping_file.readlines()
    mapping = dict([_.split("\t")[0:2] for _ in lines])
    mapping = {k.strip(): v.strip() for k, v in mapping.items()}
  return mapping
