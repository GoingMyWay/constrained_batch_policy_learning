"""
Created on December 12, 2018

@author: clvoloshin, 
"""
from fitted_algo import FittedAlgo
import numpy as np
from tqdm import tqdm
from env_nn import *
from thread_safe import threadsafe_generator

class LakeFittedQEvaluation(FittedAlgo):
    def __init__(self, initial_states, num_inputs, grid_shape, dim_of_actions, max_epochs, gamma,model_type='mlp', position_of_goals=None, position_of_holes=None, num_frame_stack=None):

        '''
        An implementation of fitted Q iteration

        num_inputs: number of inputs
        dim_of_actions: dimension of action space
        max_epochs: positive int, specifies how many iterations to run the algorithm
        gamma: discount factor
        '''
        self.model_type = model_type
        self.initial_states = initial_states
        self.num_inputs = num_inputs
        self.dim_of_actions = dim_of_actions
        self.max_epochs = max_epochs
        self.gamma = gamma
        self.grid_shape = grid_shape
        self.position_of_holes = position_of_holes
        self.position_of_goals = position_of_goals
        self.num_frame_stack = num_frame_stack

        super(LakeFittedQEvaluation, self).__init__()

    def run(self, policy, which_cost, dataset, epochs=500, epsilon=1e-8, desc='FQE', g_idx=None, **kw):
        # dataset is the original dataset generated by pi_{old} to which we will find
        # an approximately optimal Q

        self.Q_k = self.init_Q(model_type=self.model_type, position_of_holes=self.position_of_holes, position_of_goals=self.position_of_goals, num_frame_stack=self.num_frame_stack, **kw)

        X_a = np.hstack(dataset.get_state_action_pairs())
        x_prime = dataset['x_prime']

        index_of_skim = self.skim(X_a, x_prime)
        X_a = X_a[index_of_skim]
        x_prime = x_prime[index_of_skim][:,0]
        dataset.set_cost(which_cost, idx=g_idx)
        dataset_costs = dataset['cost'][index_of_skim]
        dones = dataset['done'][index_of_skim]

        for k in tqdm(range(self.max_epochs), desc=desc):

            # {((x,a), r+gamma* Q(x',pi(x')))}
            
            # if k == 0:
            #     # Q_0 = 0 everywhere
            #     costs = dataset_costs
            # else:
            costs = dataset_costs + (self.gamma*self.Q_k(x_prime, policy(x_prime)).reshape(-1)*(1-dones.astype(int))).reshape(-1)

            self.fit(X_a, costs, epochs=epochs, batch_size=X_a.shape[0], epsilon=epsilon, evaluate=False, verbose=0)

            # if not self.Q_k.callbacks_list[0].converged:
            #     print 'Continuing training due to lack of convergence'
            #     self.fit(X_a, costs, epochs=epochs, batch_size=X_a.shape[0], epsilon=epsilon, evaluate=False, verbose=0)


        return np.mean([self.Q_k(state, policy(state)) for state in self.initial_states])

    def init_Q(self, epsilon=1e-10, **kw):
        return LakeNN(self.num_inputs, 1, self.grid_shape, self.dim_of_actions, self.gamma, epsilon, **kw)

class CarFittedQEvaluation(FittedAlgo):
    def __init__(self, state_space_dim, dim_of_actions, max_epochs, gamma, model_type='cnn', num_frame_stack=None):

        '''
        An implementation of fitted Q iteration

        num_inputs: number of inputs
        dim_of_actions: dimension of action space
        max_epochs: positive int, specifies how many iterations to run the algorithm
        gamma: discount factor
        '''
        self.model_type = model_type


        self.state_space_dim = state_space_dim
        self.dim_of_actions = dim_of_actions
        self.max_epochs = max_epochs
        self.gamma = gamma
        self.num_frame_stack = num_frame_stack
        self.Q_k = None
        self.Q_k_minus_1 = None

        super(CarFittedQEvaluation, self).__init__()

    def run(self, policy, which_cost, dataset, epochs=100, epsilon=1e-8, desc='FQE', g_idx=None, **kw):
        # dataset is the original dataset generated by pi_{old} to which we will find
        # an approximately optimal Q
        
        dataset.set_cost(which_cost, idx=g_idx)
        
        if self.Q_k is None:
            self.Q_k = self.init_Q(model_type=self.model_type, num_frame_stack=self.num_frame_stack, **kw)
            self.Q_k_minus_1 = self.init_Q(model_type=self.model_type, num_frame_stack=self.num_frame_stack, **kw)
            x_prime = np.rollaxis(dataset['frames'][dataset['next_states'][[0]]], 1,4)
            self.Q_k.min_over_a([x_prime], x_preprocessed=True)[0]
            self.Q_k_minus_1.min_over_a([x_prime], x_preprocessed=True)[0]
            self.Q_k.copy_over_to(self.Q_k_minus_1)
        
        # setting up graph. Why do i need to do this?!
        # self.Q_k_minus_1(dataset['x'][0][np.newaxis,...], [0])
        # self.Q_k(dataset['x'][0][np.newaxis,...], [0])

        for k in tqdm(range(self.max_epochs), desc=desc):
            batch_size = 64
            steps_per_epoch = 1 #int(np.ceil(len(dataset)/float(batch_size)))
            dataset_length = len(dataset)
            gen = self.data_generator(dataset, policy, fixed_permutation=False, batch_size=batch_size)
            self.fit_generator(gen, epochs=epochs, steps_per_epoch=steps_per_epoch, max_queue_size=10, workers=3, use_multiprocessing=False, epsilon=epsilon, evaluate=False, verbose=2)
            self.Q_k.copy_over_to(self.Q_k_minus_1)

        try:
            initial_states = np.unique([episode.frames[[0]*episode.num_frame_stack] for episode in dataset.episodes], axis=0)
        except:
            initial_states = dataset['x_repr'][[0]*dataset.num_frame_stack]
        
        # initial_states = self.Q_k.representation(initial_states)
        actions = policy(initial_states, x_preprocessed = True)
        Q_val = self.Q_k.all_actions(initial_states, x_preprocessed=True)[np.arange(len(actions)), actions]
        return np.mean(Q_val)

    @threadsafe_generator
    def data_generator(self, dataset, policy, fixed_permutation=False, batch_size = 64):
        data_length = len(dataset)
        steps = int(np.ceil(data_length/float(batch_size)))
        i = -1
        amount_of_data_calcd = 0
        if fixed_permutation:
            random_permutation = np.random.permutation(np.arange(data_length))
            calcd_costs = np.empty((len(dataset),), dtype='float64')
        while True:
            i = (i + 1) % steps
            # print 'Getting batch: %s to %s' % ((i*batch_size),((i+1)*batch_size))
            if fixed_permutation:
                batch_idxs = random_permutation[(i*batch_size):((i+1)*batch_size)]
            else:
                batch_idxs = np.random.choice(np.arange(data_length), batch_size)
            
            amount_of_data_calcd += len(batch_idxs)
            # import pdb; pdb.set_trace()  
            
            X =  np.rollaxis(dataset['frames'][dataset['prev_states'][batch_idxs]],1,4)
            actions = np.atleast_2d(dataset['a'][batch_idxs]).T
            x_prime =  np.rollaxis(dataset['frames'][dataset['next_states'][batch_idxs]],1,4)
            dataset_costs = dataset['cost'][batch_idxs]
            dones = dataset['done'][batch_idxs]

            if fixed_permutation:
                if amount_of_data_calcd <= data_length:
                    actions = policy(x_prime, x_preprocessed = True)
                    Q_val = self.Q_k_minus_1.all_actions(x_prime, x_preprocessed=True)[np.arange(len(actions)), actions]
                    costs = dataset_costs + (self.gamma*Q_val.reshape(-1)*(1-dones.astype(int))).reshape(-1)
                    calcd_costs[batch_idxs] = costs
                else:
                    costs = calcd_costs[batch_idxs]
            else:
                actions = policy(x_prime, x_preprocessed = True)
                Q_val = self.Q_k_minus_1.all_actions(x_prime, x_preprocessed=True)[np.arange(len(actions)), actions]
                costs = dataset_costs + (self.gamma*Q_val.reshape(-1)*(1-dones.astype(int))).reshape(-1)
                    

            X = self.Q_k_minus_1.representation([X], actions, x_preprocessed=True)

            yield (X, costs)

    def init_Q(self, epsilon=1e-10, **kw):
        return CarNN(self.state_space_dim, self.dim_of_actions, self.gamma, convergence_of_model_epsilon=epsilon, **kw)

