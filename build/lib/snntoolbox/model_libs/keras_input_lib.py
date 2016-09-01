# -*- coding: utf-8 -*-
"""
Methods to extract an input model written in Keras and prepare it for further
processing in the SNN toolbox.

The idea is to make all further steps in the conversion/simulation pipeline
independent of the original model format. Therefore, when a developer adds a
new input model library (e.g. Caffe) to the toolbox, the following methods must
be implemented and satisfy the return requirements specified in their
respective docstrings:

    - extract
    - load_ann
    - evaluate

Created on Thu May 19 08:21:05 2016

@author: rbodo
"""

import os
from snntoolbox.config import settings, spiking_layers
from snntoolbox.model_libs.common import absorb_bn


def extract(model):
    """Extract the essential information about a neural network.

    This method serves to abstract the conversion process of a network from the
    language the input model was built in (e.g. Keras or Lasagne).

    To extend the toolbox by another input format (e.g. Caffe), this method has
    to be implemented for the respective model library.

    Implementation details:
    The methods iterates over all layers of the input model and writes the
    layer specifications and parameters into a dictionary. The keys are chosen
    in accordance with Keras layer attributes to facilitate instantiation of a
    new, parsed Keras model (done in a later step by the method
    ``core.util.parse``).

    This function applies several simplifications and adaptations to prepare
    the model for conversion to spiking. These modifications include:

    - Removing layers only used during training (Dropout, BatchNormalization,
      ...)
    - Absorbing the parameters of BatchNormalization layers into the parameters
      of the preceeding layer. This does not affect performance because
      batch-norm-parameters are constant at inference time.
    - Removing ReLU activation layers, because their function is inherent to
      the spike generation mechanism. The information which nonlinearity was
      used in the original model is preserved in the layer specifications of
      the parsed model. If the output layer employs the softmax function, a
      spiking version is used when testing the SNN in INIsim or MegaSim
      simulators.
    - Inserting a Flatten layer between Conv and FC layers, if the input model
      did not explicitly include one.

    Parameters
    ----------

    model: dict
        A dictionary of objects that constitute the input model. Contains at
        least the key
            - ``model``: A model instance of the network in the respective
              ``model_lib``.
        For instance, if the input model was written using Keras, the 'model'-
        value would be an instance of ``keras.Model``.

    Returns
    -------

    Dictionary containing the parsed network specifications.

    layers: list
        List of all the layers of the network, where each layer contains a
        dictionary with keys

        - layer_type (string): Describing the type, e.g. `Dense`,
          `Convolution`, `Pool`.

        In addition, `Dense` and `Convolution` layer types contain

        - parameters (array): The weights and biases connecting this layer with
          the previous.

        `Convolution` layers contain further

        - nb_col (int): The x-dimension of filters.
        - nb_row (int): The y-dimension of filters.
        - border_mode (string): How to handle borders during convolution, e.g.
          `full`, `valid`, `same`.

        `Pooling` layers contain

        - pool_size (list): Specifies the subsampling factor in each dimension.
        - strides (list): The stepsize in each dimension during pooling.
    """

    layers = []
    for (layer_num, layer) in enumerate(model.layers):
        layer_type = layer.__class__.__name__

        # Absorb BatchNormalization layer into parameters of previous layer
        if 'BatchNormalization' in layer_type:
            bn_parameters = layer.get_weights()  # gamma, beta, mean, std
            for k in [layer_num - i for i in range(1, 3)]:
                prev_layer = layers[k]
                if prev_layer.weights != []:
                    break
            parameters = prev_layer.get_weights()  # W, b of next layer
            print("Absorbing batch-normalization parameters into " +
                  "parameters of layer {}, {}.".format(k, prev_layer.name))
            layers[-1]['parameters'] = absorb_bn(
                parameters[0], parameters[1], bn_parameters[0],
                bn_parameters[1], bn_parameters[2], bn_parameters[3],
                layer.epsilon)

        if layer_type not in spiking_layers:
            print("Skipping layer {}".format(layer_type))
            continue

        print("Parsing layer {}".format(layer_type))

        if layer_num == 0:
            batch_input_shape = list(layer.batch_input_shape)
            batch_input_shape[0] = settings['batch_size']
            layer.batch_input_shape = tuple(batch_input_shape)

        attributes = layer.get_config()
        attributes['layer_type'] = layer.__class__.__name__

        # Append layer name
        if len(layer.output_shape) == 2:
            shape_string = '_{}'.format(layer.output_shape[1])
        else:
            shape_string = '_{}x{}x{}'.format(layer.output_shape[1],
                                              layer.output_shape[2],
                                              layer.output_shape[3])
        num_str = str(layer_num) if layer_num > 9 else '0' + str(layer_num)
        attributes['name'] = num_str + layer_type + shape_string

        if layer_type in {'Dense', 'Convolution2D'}:
            attributes['parameters'] = layer.get_weights()
            # Get type of nonlinearity if the activation is directly in the
            # Dense / Conv layer:
            activation = layer.get_config()['activation']
            # Otherwise, search for the activation layer:
            for k in range(layer_num+1, min(layer_num+4, len(model.layers))):
                if model.layers[k].__class__.__name__ == 'Activation':
                    activation = model.layers[k].get_config()['activation']
                    break
            attributes['activation'] = activation
            print("Detected activation {}".format(activation))

        layers.append(attributes)

    return layers


def load_ann(path=None, filename=None):
    """Load network from file.

    Parameters
    ----------

    path: string, optional
        Path to directory where to load model from. Defaults to
        ``settings['path']``.

    filename: string, optional
        Name of file to load model from. Defaults to ``settings['filename']``.

    Returns
    -------

    model: dict
        A dictionary of objects that constitute the input model. It must
        contain the following two keys:

        - 'model': Model instance of the network in the respective
          ``model_lib``.
        - 'val_fn': Theano function that allows evaluating the original model.

        For instance, if the input model was written using Keras, the 'model'-
        value would be an instance of ``keras.Model``, and 'val_fn' the
        ``keras.Model.evaluate`` method.

    """

    from keras import models

    if path is None:
        path = settings['path']
    if filename is None:
        filename = settings['filename']
    filepath = os.path.join(path, filename)

    if os.path.exists(filepath + '.json'):
        model = models.model_from_json(open(filepath + '.json').read())
        model.load_weights(filepath + '.h5')
        # With this loading method, optimizer and loss cannot be recovered.
        # Could be specified by user, but since they are not really needed
        # at inference time, set them to the most common choice.
        model.compile('sgd', 'categorical_crossentropy', metrics=['accuracy'])
    else:
        model = models.load_ann(filepath + '.h5')

    return {'model': model, 'val_fn': model.evaluate}


def evaluate(val_fn, X_test, Y_test):
    """Evaluate the original ANN."""

    return val_fn(X_test, Y_test)