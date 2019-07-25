#%% IMPORTING/SETTING UP PATHS

import sys
import os
import tensorflow as tf
import numpy as np
import matplotlib.pyplot as plt

# Manually making sure the numpy random seeds are "the same" on all devices, for reproducibility in random processes
np.random.seed(1234)
# Same for tensorflow
tf.random.set_seed(1234)

#%% LOCAL IMPORTS
sys.path.append("1d-burgers")
from burgersutil import prep_data, Logger, plot_disc_results, appDataPath

#%% HYPER PARAMETERS

# Data size on initial condition on u
N_0 = 199
N_1 = 201
# DeepNN topology (1-sized input [x], 3 hidden layer of 50-width, q-sized output defined later [u_1^n(x), ..., u_{q+1}^n(x)]
layers = [1, 50, 50, 50, 0]
# Creating the optimizer
optimizer = tf.keras.optimizers.Adam(lr=0.001, beta_1=0.9, beta_2=0.999, epsilon=1e-08)
epochs = 5000

#%% DEFINING THE MODEL

class PhysicsInformedNN(object):
  def __init__(self, layers, optimizer, logger, dt, lb, ub, q, IRK_alpha, IRK_beta):
    self.lb = lb
    self.ub = ub

    self.dt = dt

    self.q = max(q,1)
    self.IRK_alpha = IRK_alpha
    self.IRK_beta = IRK_beta

    # New descriptive Keras model [2, 50, …, 50, q+1]
    self.U_model = tf.keras.Sequential()
    self.U_model.add(tf.keras.layers.InputLayer(input_shape=(layers[0],)))
    for width in layers[1:]:
        self.U_model.add(tf.keras.layers.Dense(width,
          activation=tf.nn.tanh, kernel_initializer='glorot_normal'))

    self.dtype = tf.float32

    self.optimizer = optimizer
    self.logger = logger

  def U_0_model(self, x):
    # Using the new GradientTape paradigm of TF2.0,
    # which keeps track of operations to get the gradient at runtime
    with tf.GradientTape(persistent=True) as tape:
      # Watching the two inputs we’ll need later, x and t
      tape.watch(x)
      tape.watch(self.dummy_x0_tf)

      # Getting the prediction, and removing the last item (q+1)
      U = self.U_model(x) # shape=(len(x), q+1)

      # Deriving INSIDE the tape (2-step-dummy grad technique because U is a mat)
      g_U = tape.gradient(U, x, output_gradients=self.dummy_x0_tf)
      U_x = tape.gradient(g_U, self.dummy_x0_tf)
      g_U_x = tape.gradient(U_x, x, output_gradients=self.dummy_x0_tf)
    
    # Doing the last one outside the with, to optimize performance
    # Impossible to do for the earlier grad, because they’re needed after
    U_xx = tape.gradient(g_U_x, self.dummy_x0_tf)

    # Letting the tape go
    del tape

    # Buidling the PINNs, shape = (len(x), q+1), IRK shape = (q, q+1)
    l1 = self.lambda_1
    l2 = tf.exp(self.lambda_2)
    N = l1*U*U_x - l2U_xx # shape=(len(x), q)
    return U + self.dt*tf.matmul(N, self.IRK_alpha.T)

  def U_1_model(self, x):
    # Using the new GradientTape paradigm of TF2.0,
    # which keeps track of operations to get the gradient at runtime
    with tf.GradientTape(persistent=True) as tape:
      # Watching the two inputs we’ll need later, x and t
      tape.watch(x)
      tape.watch(self.dummy_x1_tf)

      # Getting the prediction, and removing the last item (q+1)
      U = self.U_model(x) # shape=(len(x), q+1)

      # Deriving INSIDE the tape (2-step-dummy grad technique because U is a mat)
      g_U = tape.gradient(U, x, output_gradients=self.dummy_x1_tf)
      U_x = tape.gradient(g_U, self.dummy_x1_tf)
      g_U_x = tape.gradient(U_x, x, output_gradients=self.dummy_x1_tf)
    
    # Doing the last one outside the with, to optimize performance
    # Impossible to do for the earlier grad, because they’re needed after
    U_xx = tape.gradient(g_U_x, self.dummy_x1_tf)

    # Letting the tape go
    del tape

    # Buidling the PINNs, shape = (len(x), q+1), IRK shape = (q, q+1)
    l1 = self.lambda_1
    l2 = tf.exp(self.lambda_2)
    N = l1*U*U_x - l2*U_xx # shape=(len(x), q)
    return U + self.dt*tf.matmul(N, self.IRK_alpha.T)

  def U_0_model(self, x):
    # Using the new GradientTape paradigm of TF2.0,
    # which keeps track of operations to get the gradient at runtime
    with tf.GradientTape(persistent=True) as tape:
      # Watching the two inputs we’ll need later, x and t
      tape.watch(x)
      tape.watch(self.dummy_x0_tf)

      # Getting the prediction, and removing the last item (q+1)
      U = self.U_model(x) # shape=(len(x), q+1)

      # Deriving INSIDE the tape (2-step-dummy grad technique because U is a mat)
      g_U = tape.gradient(U, x, output_gradients=self.dummy_x0_tf)
      U_x = tape.gradient(g_U, self.dummy_x0_tf)
      g_U_x = tape.gradient(U_x, x, output_gradients=self.dummy_x0_tf)
    
    # Doing the last one outside the with, to optimize performance
    # Impossible to do for the earlier grad, because they’re needed after
    U_xx = tape.gradient(g_U_x, self.dummy_x0_tf)

    # Letting the tape go
    del tape

    # Buidling the PINNs, shape = (len(x), q+1), IRK shape = (q, q+1)
    l1 = self.lambda_1
    l2 = tf.exp(self.lambda_2)
    N = l1*U*U_x - l2*U_xx # shape=(len(x), q)
    return U + self.dt*tf.matmul(N, self.IRK_alpha.T)

  # Defining custom loss
  def __loss(self, x_0, u_0, x_1, u_1):
    u_0_pred = self.U_0_model(x_0)
    u_1_pred = self.U_model(x_1)
    return tf.reduce_sum(tf.square(u_0_pred - u_0)) + \
      tf.reduce_sum(tf.square(u_1_pred - u_1))

  def __grad(self, x_0, u_0, x_1, u_1):
    with tf.GradientTape() as tape:
      loss_value = self.__loss(x_0, u_0, x_1, u_1)
    return loss_value, tape.gradient(loss_value, self.__wrap_training_variables())

  def __wrap_training_variables(self):
    var = self.U_model.trainable_variables
    return var

  def get_params(self, numpy=False):
    l1 = self.lambda_1
    l2 = tf.exp(self.lambda_2)
    if numpy:
      return l1.numpy()[0], l2.numpy()[0]
    return l1, l2

  def error(self, x_star, u_star):
    # u_pred = self.predict(x_star)
    # return np.linalg.norm(u_pred-u_star,2)/np.linalg.norm(u_star,2)
    return 0.0

  def summary(self):
    return self.U_model.summary()

  # The training function
  def fit(self, x_0, u_0, x_1, u_1, epochs=1, log_epochs=50):
    self.logger.log_train_start(self)

    # Creating the tensors
    self.x_0 = tf.convert_to_tensor(x_0, dtype=self.dtype)
    self.u_0 = tf.convert_to_tensor(u_0, dtype=self.dtype)
    self.x_1 = tf.convert_to_tensor(x_1, dtype=self.dtype)
    self.u_1 = tf.convert_to_tensor(u_1, dtype=self.dtype)

    self.lambda_1 = tf.Variable([0.0], dtype=self.dtype)
    self.lambda_2 = tf.Variable([-6.0], dtype=self.dtype)

    # Creating dummy tensors for the gradients
    self.dummy_x0_tf = tf.ones([1, self.q], dtype=self.dtype)
    self.dummy_x1_tf = tf.ones([1, self.q], dtype=self.dtype)

    # Training loop
    for epoch in range(epochs):
      # Optimization step
      loss_value, grads = self.__grad(self.x_0, self.u_0, self.x_1, self.u_1)
      self.optimizer.apply_gradients(
        zip(grads, self.__wrap_training_variables()))

      # Logging every so often
      if epoch % log_epochs == 0:
        l1, l2 = self.get_params(numpy=True)
        custom = f"l1 = {l1:5f}  l2 = {l2:8f}"
        self.logger.log_train_epoch(epoch, loss_value, custom)
    
    self.logger.log_train_end(epochs)

  def predict(self, x_star):
    u_star = self.U_model(x_star)[:, -1]
    return u_star

#%% TRAINING THE MODEL

# Setup
lb = np.array([-1.0])
ub = np.array([1.0])
idx_t_0 = 10
skip = 80
idx_t_1 = idx_t_0 + skip

# Getting the data
path = os.path.join(appDataPath, "burgers_shock.mat")
x_0, u_0, x_1, u_1, dt, q, \
  Exact_u, IRK_alpha, IRK_beta = prep_data(path, N_0=N_0, N_1=N_1, lb=lb, ub=ub, noise=0.0, idx_t_0=idx_t_0, idx_t_1=idx_t_1)

layers[-1] = q

logger = Logger(1.0, 0.01/np.pi)

# Creating the model and training
pinn = PhysicsInformedNN(layers, optimizer, logger, dt, lb, ub, q, IRK_alpha, IRK_beta)
pinn.fit(x_0, u_0, x_1, epochs)

# Getting the model predictions, from the same (x,t) that the predictions were previously gotten from
#u_1_pred = pinn.predict(x_star)

#%% PLOTTING
#plot_disc_results(x_star, idx_t_0, idx_t_1, x_0, u_0, ub, lb, u_1_pred, Exact_u, x, t)