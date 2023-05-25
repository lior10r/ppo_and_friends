import gymnasium as gym
from ppo_and_friends.environments.gym.wrappers import SingleAgentGymWrapper
from ppo_and_friends.policies.utils import get_single_policy_defaults
from ppo_and_friends.runners.env_runner import GymRunner
from ppo_and_friends.networks.actor_critic_networks import FeedForwardNetwork
from ppo_and_friends.utils.schedulers import *
import torch.nn as nn

class LunarLanderContinuousRunner(GymRunner):

    def run(self):
        env_generator = lambda : \
            SingleAgentGymWrapper(gym.make('LunarLanderContinuous-v2',
                render_mode = self.get_gym_render_mode()))

        #
        # Lunar lander observations are organized as follows:
        #    Positions: 2
        #    Positional velocities: 2
        #    Angle: 1
        #    Angular velocities: 1
        #    Leg contact: 2
        #
        actor_kw_args = {}

        #
        # Extra args for the actor critic models.
        # I find that leaky relu does much better with the lunar
        # lander env.
        #
        actor_kw_args["activation"]  = nn.LeakyReLU()
        actor_kw_args["hidden_size"] = 64

        critic_kw_args = actor_kw_args.copy()
        critic_kw_args["hidden_size"] = 256

        lr = LinearScheduler(
            status_key    = "iteration",
            status_max    = 100,
            max_value     = 0.0003,
            min_value     = 0.0001)

        #
        # Running with 2 processors works well here.
        #
        ts_per_rollout = self.get_adjusted_ts_per_rollout(1024)

        policy_args = {\
            "ac_network"       : FeedForwardNetwork,
            "actor_kw_args"    : actor_kw_args,
            "critic_kw_args"   : critic_kw_args,
            "lr"               : lr,
            "bootstrap_clip"   : (-10., 10.),
            "target_kl"        : 0.015,
        }

        policy_settings, policy_mapping_fn = get_single_policy_defaults(
            env_generator = env_generator,
            policy_args   = policy_args)

        save_when = ChangeInStateScheduler(
            status_key     = "extrinsic score avg",
            status_preface = "single_agent",
            compare_fn     = np.greater_equal,
            persistent     = True)

        self.run_ppo(env_generator       = env_generator,
                     save_when           = save_when,
                     policy_settings     = policy_settings,
                     policy_mapping_fn   = policy_mapping_fn,
                     max_ts_per_ep       = 32,
                     ts_per_rollout      = ts_per_rollout,
                     epochs_per_iter     = 16,
                     batch_size          = 512,
                     normalize_obs       = True,
                     normalize_rewards   = True,
                     obs_clip            = (-10., 10.),
                     reward_clip         = (-10., 10.),
                     **self.kw_run_args)
