from network import LinearNN
import sys
import numpy as np
import os
from torch.distributions import MultivariateNormal
from torch.distributions import Categorical
import torch
from torch.optim import Adam
from torch import nn


class PPO(object):

    def __init__(self,
                 env,
                 device,
                 action_type,
                 render       = False,
                 load_weights = False,
                 model_path   = "./"):
        #FIXME: this is currently setup for a continuous action
        # space. Let's have an option for a discrete action space.
        # We would use a softmax in the network output, and we
        # would sample from that output based on the probabilities.

        if np.issubdtype(env.action_space.dtype, np.floating):
            self.act_dim = env.action_space.shape[0]
        elif np.issubdtype(env.action_space.dtype, np.integer):
            self.act_dim = env.action_space.n

        self.obs_dim     = env.observation_space.shape[0]
        self.env         = env
        self.device      = device
        self.model_path  = model_path
        self.render      = render
        self.action_type = action_type

        self.actor  = LinearNN(
            "actor", 
            self.obs_dim, 
            self.act_dim, 
            action_type)
        self.critic = LinearNN(
            "critic", 
            self.obs_dim, 
            1,
            action_type)

        self.actor  = self.actor.to(device)
        self.critic = self.critic.to(device)

        if load_weights:
            if not os.path.exists(model_path):
                msg  = "ERROR: model_path does not exist. Unable "
                msg += "to load weights!"
                sys.exit(1)

            self.load()

        self._init_hyperparameters()

        self.cov_var = torch.full(size=(self.act_dim,), fill_value=0.5)
        self.cov_mat = torch.diag(self.cov_var)

        self.actor_optim  = Adam(self.actor.parameters(), lr=self.lr)
        self.critic_optim = Adam(self.critic.parameters(), lr=self.lr)

        if not os.path.exists(model_path):
            os.makedirs(model_path)

    def _init_hyperparameters(self):
        self.timesteps_per_batch = 2048
        self.max_timesteps_per_episode = 200
        self.gamma = 0.99
        self.epochs_per_iteration = 10
        self.clip = 0.2
        self.lr = 0.0001

    def get_action(self, obs):

        if self.action_type == "continuous":
            t_obs = torch.tensor(obs).to(self.device)
            mean_action = self.actor(t_obs).cpu().detach()

            dist     = MultivariateNormal(mean_action, self.cov_mat)
            action   = dist.sample()
            log_prob = dist.log_prob(action)

        elif self.action_type == "discrete":
            t_obs = torch.tensor(obs).to(self.device)
            probs = self.actor(t_obs).cpu().detach()

            dist     = Categorical(probs)
            action   = dist.sample()
            log_prob = dist.log_prob(action)
            action   = action.int().unsqueeze(0)

        return action.detach().numpy(), log_prob.detach().to(self.device)

    def compute_rewards_tg(self, batch_rewards):
        batch_rewards_tg = []

        for ep_rewards in reversed(batch_rewards):
            discounted_reward = 0

            for reward in reversed(ep_rewards):

                discounted_reward = reward + discounted_reward * self.gamma

                #FIXME: we can be a lot more effecient here.
                batch_rewards_tg.insert(0, discounted_reward)

        batch_rewards_tg = torch.tensor(batch_rewards_tg, dtype=torch.float).to(self.device)
        return batch_rewards_tg

    def evaluate(self, batch_obs, batch_actions):
        value = self.critic(batch_obs).squeeze()

        if self.action_type == "continuous":
            mean = self.actor(batch_obs).cpu()
            dist = MultivariateNormal(mean, self.cov_mat)
            log_probs = dist.log_prob(batch_actions.cpu())

        elif self.action_type == "discrete":
            probs = self.actor(batch_obs).cpu().detach()
            dist      = Categorical(probs)
            action    = dist.sample()
            log_probs = dist.log_prob(action)

        return value, log_probs.to(self.device)

    def rollout(self):
        batch_obs        = [] # observations.
        batch_actions    = [] # actions.
        batch_log_probs  = [] # log probs of each action.
        batch_rewards    = [] # rewards.
        batch_rewards_tg = [] # rewards to go.
        batch_ep_lens    = [] # episode lengths.

        total_ts = 0
        while total_ts < self.timesteps_per_batch:

            ep_rewards = []
            obs  = self.env.reset()
            done = False

            for ts in range(self.max_timesteps_per_episode):
                if self.render:
                    self.env.render()

                total_ts += 1

                batch_obs.append(obs)
                action, log_prob = self.get_action(obs)

                #FIXME: can we make this cleaner?
                if self.action_type == "discrete":
                    obs, reward, done, _ = self.env.step(action[0])
                else:
                    obs, reward, done, _ = self.env.step(action)

                ep_rewards.append(reward)
                batch_actions.append(action)
                batch_log_probs.append(log_prob)

                if done:
                    break

            batch_ep_lens.append(ts + 1)
            batch_rewards.append(ep_rewards)

        batch_obs       = torch.tensor(batch_obs, dtype=torch.float).to(self.device)
        batch_log_probs = torch.tensor(batch_log_probs, dtype=torch.float).to(self.device)

        if self.action_type == "continuous":
            batch_actions = torch.tensor(batch_actions, dtype=torch.float).to(self.device)
        elif self.action_type == "discrete":
            batch_actions = torch.tensor(batch_actions, dtype=torch.int32).to(self.device)

        batch_rewards_tg = self.compute_rewards_tg(batch_rewards).to(self.device)

        return batch_obs, batch_actions, batch_log_probs, batch_rewards_tg,\
            batch_ep_lens

    def learn(self, total_timesteps):

        t_so_far = 0
        while t_so_far < total_timesteps:
            batch_obs, batch_actions, batch_log_probs, \
                batch_rewards_tg, batch_ep_lens = self.rollout()

            t_so_far += np.sum(batch_ep_lens)

            value, _  = self.evaluate(batch_obs, batch_actions)
            advantage = batch_rewards_tg - value.detach()

            advantage = (advantage - advantage.mean()) / \
                (advantage.std() + 1e-10)

            for _ in range(self.epochs_per_iteration):
                value, curr_log_probs = self.evaluate(batch_obs, batch_actions)

                # new p / old p
                ratios = torch.exp(curr_log_probs - batch_log_probs)
                surr1  = ratios * advantage
                # TODO: hmm... I thought the advantage was supposed to be inside
                # the clip?
                surr2  = torch.clamp(ratios, 1 - self.clip, 1 + self.clip) * advantage

                actor_loss  = (-torch.min(surr1, surr2)).mean()
                critic_loss = nn.MSELoss()(value, batch_rewards_tg)

                self.actor_optim.zero_grad()
                actor_loss.backward(retain_graph=True)
                self.actor_optim.step()

                self.critic_optim.zero_grad()
                critic_loss.backward()
                self.critic_optim.step()

        self.save()


    def save(self):
        self.actor.save(self.model_path)
        self.critic.save(self.model_path)

    def load(self):
        self.actor.load(self.model_path)
        self.critic.load(self.model_path)
