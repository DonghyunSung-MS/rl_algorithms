
import os
import copy
import numpy as np
import wandb
import time
import torch
import torch.nn as nn
import torch.optim as optim
import matplotlib.pyplot as plt
#from collections import deque

from dm_control import viewer
import utills.logger as logger
from utills.trajectoryBuffer import *
import utills.rl_utills as rl_utills
from agents.core import Actor, Critic

from abc import ABC, abstractmethod

class Agent(ABC):
    def __init__(self, env, configs, args):
        self.benchmark = args.benchmark
        self._env = env
        self._logger = None
        """argument to self value"""
        self.render = configs.render
        self.img = None

        torch.manual_seed(args.seed)
        np.random.seed(args.seed)
        self.wandb = args.wandb

        #Hyperparameters depend on algorithms
        self.set_own_hyper(configs)

        self.log_dir = configs.log_dir
        self.log_interval = configs.log_interval

        self.model_dir = configs.model_dir
        self.save_interval = configs.save_interval

        self.max_iter = configs.max_iter
        self.batch_size = configs.batch_size

        self.total_sample_size = configs.total_sample_size
        self.test_iter = configs.test_iter

        self.gamma = configs.gamma
        self.lamda = configs.lamda
        self.actor_lr = configs.actor_lr
        self.critic_lr = configs.critic_lr


        self.state_dim = None
        self.action_dim = None

        if self.benchmark == "dm_control":
            self.state_dim = 0
            for k, v in self._env.observation_spec().items():
                try:
                    self.state_dim += v.shape[0]
                except:
                    self.state_dim += 1
            self.action_dim = self._env.action_spec().shape[0]
            print("State spec : ",self._env.observation_spec())
            print("Action spec: ",self._env.action_spec())
        elif self.benchmark == "gym":
            self.state_dim = self._env.observation_space.shape[0]
            self.action_dim = self._env.action_space.shape[0]
            print("state_size : ", self.state_dim )
            print("action_size : ", self.action_dim)

        self.dev = None
        if configs.gpu:
            self.dev = torch.device("cuda:0")
        else:
            self.dev = torch.device("cpu")

        self._actor = Actor(self.state_dim, self.action_dim, configs).to(self.dev)
        self._critic = Critic(self.state_dim, configs).to(self.dev)

        self.actor_optim = optim.Adam(self._actor.parameters(), lr=self.actor_lr)
        self.critic_optim = optim.Adam(self._critic.parameters(), lr=self.critic_lr)

        self.history = None
        self.global_episode = 0

        super().__init__()

    def test_interact(self, model_path, random=False):
        """load trained parameters"""
        self._actor.load_state_dict(torch.load(model_path))

        if self.benchmark == "dm_control":
            if random:
                def random_policy(time_step):
                    del time_step  # Unused.
                    return np.random.uniform(low=self._env.action_spec().minimum,
                                             high=self._env.action_spec().maximum,
                                             size=self._env.action_spec().shape)
                viewer.launch(self._env, policy=random_policy)
            else:
                def source_policy(time_step):
                    s = None
                    for k, v in time_step.observation.items():
                        if s is None:
                            s = v
                        else:
                            s = np.hstack([s, v])
                    s_3d = np.reshape(s, [1, self.state_dim])
                    mu, std = self._actor(torch.Tensor(s_3d).to(self.dev))
                    action = self._actor.get_action(mu, std)

                    return action

                viewer.launch(self._env, policy=source_policy)
        elif self.benchmark == "gym":
            for ep in range(self.test_iter):
                score = 0
                done = False
                state = self._env.reset()
                state = np.reshape(state, [1, self.state_dim])
                while not done:
                    mu, std = self._actor(torch.Tensor(state).to(self.dev))
                    action = self._actor.get_action(mu, std)

                    if random:
                        next_state, reward, done, info = self._env.step(np.random.randn(self.action_dim))
                    else:
                        next_state, reward, done, info = self._env.step(action)
                    self._env.render()

                    score += reward
                    next_state = np.reshape(next_state, [1, self.state_dim])
                    state = next_state

    @abstractmethod
    def train(self):
        pass
    @abstractmethod
    def _update(self, iter):
        pass

    @abstractmethod
    def set_own_hyper(self, args):
        pass

    def _rollout(self):
        """rollout utill sample num is larger than max samples per iteration"""

        sample_num = 0
        episode = 0
        avg_train_return = 0
        avg_steps = 0
        sum_reward_iter = 0

        while sample_num < self.total_sample_size:
            steps = 0
            total_reward_per_ep = 0
            time_step = None #dm_control
            s = None
            done = False
            if self.benchmark == "dm_control":
                time_step = self._env.reset()
                s, _ , __ = self.history.covert_time_step_data(time_step)
            elif self.benchmark == "gym":
                s = self._env.reset()
            s_3d = np.reshape(s, [1, self.state_dim])
            #print(time_step.last())
            while not done:
                tic = time.time()
                mu, std = self._actor(torch.Tensor(s_3d).to(self.dev))
                action = self._actor.get_action(mu, std)

                s_ = None
                r = 0.0
                m = 0.0

                if self.benchmark == "dm_control":
                    time_step = self._env.step(action)
                    s_, r, m = self.history.covert_time_step_data(time_step)
                    done = time_step.last()
                elif self.benchmark == "gym":
                    s_, r, done, info = self._env.step(action)
                    m = 0.0 if done else 1.0
                #print(action, s_3d, r, m)
                self.history.store_history(action, s_3d, r, m)
                s = s_
                s_3d = np.reshape(s, [1, self.state_dim])
                total_reward_per_ep += r.item(0)

                if self.render:
                    self._render(tic, steps)

                steps += 1


            episode += 1
            self.global_episode += 1
            if self.wandb:
                wandb.log({"episode":self.global_episode,
                           "Ep_total_reward": total_reward_per_ep,
                           "Ep_Avg_reward": total_reward_per_ep / steps,
                           "Ep_len": steps})
            sum_reward_iter += total_reward_per_ep
            sample_num += steps

        avg_steps = sample_num / episode
        avg_train_return = sum_reward_iter / episode
        avg_train_reward = sum_reward_iter / steps

        return sample_num, avg_train_reward, avg_train_return, avg_steps

    def _render(self, tic, steps):
        if self.benchmark == "dm_control":
            max_frame = 90

            width = 640
            height = 480
            video = np.zeros((1000, height, 2 * width, 3), dtype=np.uint8)
            video[steps] = np.hstack([self._env.physics.render(height, width, camera_id=0),
                                     self._env.physics.render(height, width, camera_id=1)])

            if steps==0:
                self.img = plt.imshow(video[steps])
            else:
                self.img.set_data(video[steps])
            toc = time.time()
            clock_dt = toc-tic
            plt.pause(max(0.01, 0.03 - clock_dt))  # Need min display time > 0.0.
            plt.draw()
        elif self.benchmark == "gym":
            self._env.render()


    def save_model(self, iter, dir):
        if not os.path.isdir(dir):
            os.makedirs(dir)

        ckpt_path_a = dir + str(iter)+'th_model_a.pth.tar'
        ckpt_path_c = dir + str(iter)+'th_model_c.pth.tar'
        torch.save(self._actor.state_dict(), ckpt_path_a)
        torch.save(self._critic.state_dict(), ckpt_path_c)