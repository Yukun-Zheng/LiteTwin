# LiteTwin implementation

The [policy package](litetwin/policy/feedback/) implements model construction, relation and progress inference, guarded execution and recovery. The [DynaMAC backbone](litetwin/policy/dynamac.py) provides the motion reference.

The [RLBench adapter](integrations/rlbench/rlbench_litetwin/) connects those components to the simulator. Its [task configuration](integrations/rlbench/configs/open_drawer/) defines which frames and drawer measurements the policy observes. The five [input trajectories](integrations/rlbench/data/demo/open_drawer/) and the [frozen checkpoint](integrations/rlbench/models/dynamac/open_drawer/) are included.

See the [demo instructions](../README.md#run-open-drawer) to build the model and run an episode. The [third-party notices](integrations/rlbench/THIRD_PARTY.md) list the external dependency revisions.
