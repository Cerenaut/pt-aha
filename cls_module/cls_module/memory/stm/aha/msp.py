"""MonosynapticPathway class."""

from collections import defaultdict

import torch
import torch.nn as nn
import torch.optim as optim
import torch.nn.functional as F

from cerenaut_pt_core.components.simple_autoencoder import SimpleAutoencoder


class MonosynapticPathway(nn.Module):
  """
  An error-driven monosynaptic pathway implementation.
  """

  def __init__(self, ca3_shape, ec_shape, config):
    super(MonosynapticPathway, self).__init__()

    self.config = config
    self.ca1_reset_params = self.config['ca1'].get('reset_params', False)
    self.ca1_reset_optim = self.config['ca1'].get('reset_optim', False)
    self.ca3_ca1_reset_params = self.config['ca3_ca1'].get('reset_params', True)
    self.ca3_ca1_reset_optim = self.config['ca3_ca1'].get('reset_optim', True)

    # Build the CA1 sub-module, to reproduce the EC inputs
    self.ca1 = SimpleAutoencoder(ec_shape, self.config['ca1'], output_shape=ec_shape)
    self.ca1_optimizer = optim.Adam(self.ca1.parameters(),
                                    lr=self.config['ca1']['learning_rate'],
                                    weight_decay=self.config['ca1']['weight_decay'])

    ca1_output_shape = [1, self.config['ca1']['num_units']]

    # Build the CA3-CA1 pathway, to recall memories stored in CA3
    self.ca3_ca1 = SimpleAutoencoder(ca3_shape, self.config['ca3_ca1'], output_shape=ca1_output_shape)
    self.ca3_ca1_optimizer = optim.Adam(self.ca3_ca1.parameters(),
                                        lr=self.config['ca3_ca1']['learning_rate'],
                                        weight_decay=self.config['ca3_ca1']['weight_decay'])

  def reset(self):
    if self.ca1_reset_params:
      self.ca1.reset_parameters()

    if self.ca3_ca1_reset_params:
      self.ca3_ca1.reset_parameters()

    # Reset the module optimizer
    if self.ca1_reset_optim:
      self.ca1_optimizer.state = defaultdict(dict)

    if self.ca3_ca1_reset_optim:
      self.ca3_ca1_optimizer.state = defaultdict(dict)

  def forward_ca1(self, inputs, targets, training=True):
    if training:
      self.ca1_optimizer.zero_grad()

    encoding, decoding = self.ca1(inputs)

    loss = F.mse_loss(decoding, targets)

    outputs = {
        'encoding': encoding.detach(),
        'decoding': decoding.detach(),

        'output': encoding.detach()  # Designated output for linked modules
    }

    if training:
      loss.backward()
      self.ca1_optimizer.step()

    return loss, outputs

  def forward_ca3_ca1(self, inputs, targets):
    if self.training:
      self.ca3_ca1_optimizer.zero_grad()

    encoding, decoding = self.ca3_ca1(inputs)

    loss = F.mse_loss(decoding, targets)

    outputs = {
        'encoding': encoding.detach(),
        'decoding': decoding.detach(),

        'output': encoding.detach()  # Designated output for linked modules
    }

    if self.training:
      loss.backward()
      self.ca3_ca1_optimizer.step()

    return loss, outputs

  def forward(self, ec_inputs, ca3_inputs):
    # During study, EC will drive the CA1
    ca1_loss, ca1_outputs = self.forward_ca1(inputs=ec_inputs, targets=ec_inputs, training=self.training)

    with torch.no_grad():
      _, post_ca1_outputs = self.forward_ca1(inputs=ec_inputs, targets=ec_inputs, training=False)
      ca3_ca1_target = post_ca1_outputs['encoding'].flatten(start_dim=1)

    ca3_ca1_loss, ca3_ca1_outputs = self.forward_ca3_ca1(inputs=ca3_inputs, targets=ca3_ca1_target)

    # During recall, the CA3:CA1 will have influence on CA1 outputs
    if not self.training and self.config['ca1']['ca3_recall']:
      ca1_encoding_recon = ca3_ca1_outputs['decoding']
      ca1_encoding_tmp = self.ca1.encode(ec_inputs)

      recall_mode = self.config['ca1'].get('ca3_recall_mode', 'ca3_only')

      if recall_mode == 'ca3_only':
        ca1_encoding = ca1_encoding_recon
      elif recall_mode == 'add':
        ca1_encoding = ca1_encoding_recon + ca1_encoding_tmp
      elif recall_mode == 'max':
        ca1_encoding = torch.max(ca1_encoding_recon, ca1_encoding_tmp)

      ca1_decoding = self.ca1.decode(ca1_encoding)
      ca1_loss = F.mse_loss(ca1_decoding, ec_inputs)

      ca1_outputs = {
        'encoding': ca1_encoding,
        'decoding': ca1_decoding,
        'output': ca1_encoding
      }

    return ca1_loss, ca1_outputs, ca3_ca1_loss, ca3_ca1_outputs
