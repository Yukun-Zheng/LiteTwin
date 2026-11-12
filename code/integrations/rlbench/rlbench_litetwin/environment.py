"""Create the RLBench environment and communicate with the LiteTwin policy worker."""

from __future__ import annotations

import hashlib
import json
import os
import select
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from integrations.rlbench.rlbench_dynamac.core.runtime import (
    DiscreteGripperProtocol,
    HYBRID_IK_CONTROLLER_PROFILE,
    GlobalIKControllerConfig,
    HybridIKControllerConfig,
    apply_gripper_for_policy_target,
    execute_hybrid_ik_ee_control,
    initialize_global_ik_controller_diagnostics,
)
from integrations.rlbench.rlbench_litetwin.protocol import litetwin_gripper_timing_metadata


EXPECTED_BASE_SCENE_SHA256 = (
    "66e1cfa0a6ee5a5e635917d23ce1b5f8ba7159ee1a5326588d798030b972306a"
)
EXPECTED_BASE_VISION_SENSORS = 10
GRIPPER_PROTOCOL = DiscreteGripperProtocol(bimanual=False)


def controller_config() -> HybridIKControllerConfig:
    return HybridIKControllerConfig()


def prepare_headless_scene(environment_module):
    original_scene = environment_module.TTT_FILE
    source = Path(original_scene)
    if not source.is_absolute():
        source = Path(environment_module.DIR_PATH) / source
    source = source.resolve()
    if hashlib.sha256(source.read_bytes()).hexdigest() != EXPECTED_BASE_SCENE_SHA256:
        raise RuntimeError("RLBench base scene does not match the configured asset")
    if not os.environ.get("QT_QPA_PLATFORM"):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from pyrep import PyRep
    from pyrep.backend import sim
    from pyrep.const import ObjectType

    temporary = tempfile.TemporaryDirectory(prefix="litetwin-lowdim-scene-")
    target = Path(temporary.name) / source.name
    simulator = PyRep()
    launched = False
    try:
        simulator.launch("", headless=True)
        launched = True
        sim.simLoadScene(str(source))
        sensors = simulator.get_objects_in_tree(object_type=ObjectType.VISION_SENSOR)
        if len(sensors) != EXPECTED_BASE_VISION_SENSORS:
            raise RuntimeError("RLBench base scene has an unexpected camera layout")
        for sensor in sensors:
            sensor.set_explicit_handling(1)
        simulator.export_scene(str(target))
    except Exception:
        temporary.cleanup()
        raise
    finally:
        if launched:
            simulator.shutdown()
    environment_module.TTT_FILE = str(target)
    restored = False

    def restore():
        nonlocal restored
        if restored:
            return
        restored = True
        environment_module.TTT_FILE = original_scene
        temporary.cleanup()

    return restore


def make_action_mode(
    config: GlobalIKControllerConfig | None = None,
    external_solver_factory=None,
):
    config = controller_config() if config is None else config
    if not isinstance(config, GlobalIKControllerConfig):
        raise TypeError("The Cartesian controller requires GlobalIKControllerConfig")
    from pyrep.const import ConfigurationPathAlgorithms
    from pyrep.errors import ConfigurationError, ConfigurationPathError, IKError
    from rlbench.action_modes.action_mode import MoveArmThenGripper
    from rlbench.action_modes.arm_action_modes import (
        ArmActionMode,
        assert_action_shape,
        assert_unit_quaternion,
    )
    from rlbench.backend.exceptions import InvalidActionError

    class AbsoluteEndEffectorIK(ArmActionMode):
        def __init__(self):
            self._external_solvers = {}
            self._last_policy_action_status = "reached"
            self._last_policy_action_statuses = {"single": "reached"}
            self._execution_diagnostics = initialize_global_ik_controller_diagnostics()
            self._execution_diagnostics.update(
                {
                    "joint_target_reached": 0,
                    "joint_target_progressed": 0,
                    "joint_target_stopped": 0,
                    "joint_target_timeouts": 0,
                    "controller_profile": HYBRID_IK_CONTROLLER_PROFILE,
                    "controller_config": config.metadata(),
                }
            )

        def policy_action_status(self):
            return self._last_policy_action_status

        def policy_action_statuses(self):
            return dict(self._last_policy_action_statuses)

        def _external_solver_for_arm(self, arm):
            key = id(arm)
            if key not in self._external_solvers:
                if external_solver_factory is None:
                    from integrations.rlbench.rlbench_dynamac.core.trac_ik import (
                        AlignedTracIKDistanceSolver,
                        TracIKDistanceConfig,
                    )

                    solver = AlignedTracIKDistanceSolver(
                        arm,
                        config=TracIKDistanceConfig(
                            timeout_s=config.trac_ik_timeout_s,
                            epsilon=config.trac_ik_epsilon,
                            translation_tolerance_m=config.trac_ik_translation_tolerance_m,
                            rotation_tolerance_rad=config.trac_ik_rotation_tolerance_rad,
                            joint_window_rad=config.trac_ik_joint_window_rad,
                            joint_delta_abs_max_rad=config.max_joint_delta_abs_rad,
                            joint_delta_l2_max_rad=config.max_joint_delta_l2_rad,
                            fk_translation_max_m=config.trac_ik_fk_translation_max_m,
                            fk_rotation_max_rad=config.trac_ik_fk_rotation_max_rad,
                        ),
                    )
                else:
                    solver = external_solver_factory(arm)
                self._external_solvers[key] = solver
            return self._external_solvers[key]

        def action(self, scene, action, ignore_collisions=True):
            del ignore_collisions
            assert_action_shape(action, (7,))
            target = np.asarray(action, dtype=np.float64)
            assert_unit_quaternion(target[3:])
            per_arm_status = {}
            try:
                status = execute_hybrid_ik_ee_control(
                    scene,
                    ((scene.robot.arm, target),),
                    config=config,
                    diagnostics=self._execution_diagnostics,
                    external_solver_factory=self._external_solver_for_arm,
                    ik_error=IKError,
                    configuration_error=ConfigurationError,
                    configuration_path_error=ConfigurationPathError,
                    invalid_action_error=InvalidActionError,
                    path_algorithm=ConfigurationPathAlgorithms.RRTConnect,
                    error_message="Cartesian end-effector control failed",
                    per_arm_status_out=per_arm_status,
                )
            except InvalidActionError:
                self._execution_diagnostics[
                    "trac_ik_distance_controller_invalid_actions"
                ] += 1
                raise
            self._execution_diagnostics[f"joint_target_{status}"] += 1
            self._last_policy_action_status = status
            self._last_policy_action_statuses = {
                "single": per_arm_status.get(id(scene.robot.arm), status)
            }
            return status

        def action_shape(self, scene):
            del scene
            return (7,)

    class PoseGripperAction(MoveArmThenGripper):
        def __init__(self, arm_action_mode, gripper_action_mode):
            super().__init__(arm_action_mode, gripper_action_mode)
            self._policy_gripper_authorization = None

        def set_policy_gripper_authorization(self, authorization):
            if authorization is None:
                self._policy_gripper_authorization = None
                return
            if set(authorization) != {"single"}:
                raise ValueError("Gripper authorization must cover the single arm")
            self._policy_gripper_authorization = authorization["single"]

        def action(self, scene, action):
            arm_size = int(np.prod(self.arm_action_mode.action_shape(scene)))
            status = self.arm_action_mode.action(
                scene,
                np.asarray(action[:arm_size]),
                bool(action[arm_size + 1 : arm_size + 2]),
            )
            authorization = self._policy_gripper_authorization
            self._policy_gripper_authorization = None
            apply_gripper_for_policy_target(
                self.gripper_action_mode,
                scene,
                np.asarray(action[arm_size : arm_size + 1]),
                arm_status=status,
                gripper_authorized=authorization,
            )

        def action_shape(self, scene):
            return super().action_shape(scene) + 1

    return PoseGripperAction(
        AbsoluteEndEffectorIK(), GRIPPER_PROTOCOL.make_action_mode()
    )


def observation_payload(observation):
    state = observation.task_low_dim_state
    if isinstance(state, tuple) and len(state) == 1:
        state = state[0]
    return {
        "gripper_pose": np.asarray(observation.gripper_pose).tolist(),
        "gripper_open": float(observation.gripper_open),
        "task_low_dim_state": np.asarray(state).reshape(-1).tolist(),
    }


class PolicyProcess:

    def __init__(
        self,
        python: Path,
        task: str,
        base_models: Path,
        litetwin_models: Path,
        task_specs: Path,
        boundary_config: Path,
        *,
        diagnostics_dir: Path | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.timeout = float(timeout)
        command = [
            str(python),
            "-m",
            "integrations.rlbench.rlbench_litetwin.policy_server",
            "serve",
            "--task",
            task,
            "--models-dir",
            str(Path(litetwin_models).resolve()),
            "--base-models-dir",
            str(Path(base_models).resolve()),
            "--task-specs",
            str(Path(task_specs).resolve()),
            "--boundary-config",
            str(Path(boundary_config).resolve()),
        ]
        if diagnostics_dir is not None:
            command.extend(["--diagnostics-dir", str(Path(diagnostics_dir).resolve())])
        self.process = subprocess.Popen(
            command,
            cwd=str(Path(__file__).resolve().parents[4]),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        response = self.request("ping")
        if not response.get("ready") or response.get("bimanual"):
            self.close()
            raise RuntimeError("Policy worker did not load a unimanual LiteTwin model")
        self.policy_steps = int(response["policy_steps"])
        self.model_identity = response["model_identity"]
        if response.get("gripper_timing") != litetwin_gripper_timing_metadata():
            self.close()
            raise RuntimeError("Policy worker gripper timing is incompatible")

    def request(self, command: str, observation=None, **fields):
        request = {"command": command, **fields}
        if observation is not None:
            request["observation"] = observation_payload(observation)
        assert self.process.stdin is not None
        assert self.process.stdout is not None
        self.process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
        self.process.stdin.flush()
        ready, _, _ = select.select([self.process.stdout], [], [], self.timeout)
        if not ready:
            self.process.terminate()
            raise TimeoutError("Policy worker response timed out")
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError("Policy worker exited without a response")
        response = json.loads(line)
        if not response.get("ok"):
            raise RuntimeError(f"Policy worker error: {response.get('error')}")
        return response

    def close(self):
        if self.process.poll() is not None:
            return
        try:
            self.request("close")
            self.process.wait(timeout=5)
        except Exception:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()


def create_environment(task_spec, observation_config):
    import rlbench.environment as environment_module

    restore = prepare_headless_scene(environment_module)
    environment = environment_module.Environment(
        action_mode=make_action_mode(),
        obs_config=observation_config,
        headless=True,
    )
    return environment, restore


__all__ = [
    "PolicyProcess",
    "controller_config",
    "create_environment",
    "make_action_mode",
    "observation_payload",
    "prepare_headless_scene",
]
