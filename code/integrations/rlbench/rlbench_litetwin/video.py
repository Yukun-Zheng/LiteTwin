"""Record simulator observations as paired-camera H.264 video."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable

import numpy as np

from integrations.rlbench.rlbench_litetwin.environment import (
    make_action_mode,
    prepare_headless_scene,
)


def observation_config(cameras, resolution):
    from rlbench.observation_config import CameraConfig, ObservationConfig

    config = ObservationConfig()
    config.set_all(False)
    config.gripper_open = True
    config.gripper_pose = True
    config.task_low_dim_state = True
    config.camera_configs = {
        camera: CameraConfig(
            rgb=True,
            depth=False,
            point_cloud=False,
            mask=False,
            image_size=tuple(resolution),
        )
        for camera in cameras
    }
    return config


def _rgb(observation, camera):
    perception = getattr(observation, "perception_data", None)
    if not isinstance(perception, dict):
        return None
    value = perception.get(f"{camera}_rgb")
    if value is None:
        return None
    array = np.asarray(value)
    if array.ndim != 3 or array.shape[2] != 3:
        raise ValueError(f"{camera} RGB frame must have shape HxWx3")
    if array.dtype != np.uint8:
        array = np.clip(array * 255.0, 0.0, 255.0).astype(np.uint8)
    return np.ascontiguousarray(array)


def _compose(observation, cameras):
    frames = []
    used = []
    for camera in cameras:
        frame = _rgb(observation, camera)
        if frame is not None:
            frames.append(frame)
            used.append(camera)
    if not frames:
        raise ValueError("Observation contains no requested RGB camera")
    if any(frame.shape[0] != frames[0].shape[0] for frame in frames):
        raise ValueError("Camera frames must have equal heights")
    return np.concatenate(frames, axis=1), tuple(used)


class FFmpegWriter:

    def __init__(self, path, *, width, height, fps, ffmpeg):
        self.path = Path(path)
        self.frames = 0
        self._process = subprocess.Popen(
            [
                str(ffmpeg),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "-video_size",
                f"{width}x{height}",
                "-framerate",
                str(fps),
                "-i",
                "-",
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(self.path),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

    def write(self, frame):
        if self._process.stdin is None:
            raise RuntimeError("FFmpeg input is closed")
        try:
            self._process.stdin.write(np.ascontiguousarray(frame).tobytes())
        except BrokenPipeError as exc:
            error = b"" if self._process.stderr is None else self._process.stderr.read()
            raise RuntimeError(error.decode(errors="replace")) from exc
        self.frames += 1

    def close(self):
        if self._process.stdin is not None and not self._process.stdin.closed:
            self._process.stdin.close()
        error = b"" if self._process.stderr is None else self._process.stderr.read()
        code = self._process.wait()
        if code != 0:
            raise RuntimeError(
                f"FFmpeg exited with code {code}: {error.decode(errors='replace')}"
            )
        if self.frames < 1 or not self.path.is_file():
            raise RuntimeError("FFmpeg did not produce a video")

    def abort(self):
        if self._process.poll() is None:
            self._process.kill()
            self._process.wait()


class ObservationRecorder:

    def __init__(self, path, *, cameras, fps, ffmpeg):
        self.path = Path(path)
        self.requested_cameras = tuple(cameras)
        self.fps = int(fps)
        self.ffmpeg = Path(ffmpeg)
        self.used_cameras = None
        self.frame_shape = None
        self._writer = None

    @property
    def frames(self):
        return 0 if self._writer is None else self._writer.frames

    def capture(self, observation):
        cameras = (
            self.requested_cameras if self.used_cameras is None else self.used_cameras
        )
        frame, used = _compose(observation, cameras)
        if self.used_cameras is None:
            if "front" not in used:
                raise ValueError("Front RGB camera is unavailable")
            self.used_cameras = used
            self.frame_shape = tuple(int(value) for value in frame.shape)
            self._writer = FFmpegWriter(
                self.path,
                width=frame.shape[1],
                height=frame.shape[0],
                fps=self.fps,
                ffmpeg=self.ffmpeg,
            )
        elif tuple(frame.shape) != self.frame_shape:
            raise ValueError("RGB frame shape changed during execution")
        self._writer.write(frame)

    def close(self):
        if self._writer is None:
            raise RuntimeError("Execution produced no RGB frames")
        self._writer.close()

    def abort(self):
        if self._writer is not None:
            self._writer.abort()


class PhysicsStepCapture:

    def __init__(self, recorder: ObservationRecorder, *, stride: int):
        if stride < 1:
            raise ValueError("Physics capture stride must be positive")
        self._recorder = recorder
        self.stride = int(stride)
        self.physics_steps = 0
        self._pyrep = None
        self._original_step: Callable[..., Any] | None = None

    def install(self, task_environment: Any):
        scene = task_environment._scene
        pyrep = getattr(scene, "pyrep", getattr(scene, "_pyrep", None))
        original_step = getattr(pyrep, "step", None)
        if not callable(original_step):
            raise RuntimeError("Task environment exposes no physics step")

        def step_with_capture(*args, **kwargs):
            result = original_step(*args, **kwargs)
            self.physics_steps += 1
            if self.physics_steps % self.stride == 0:
                self._recorder.capture(task_environment.get_observation())
            return result

        self._pyrep = pyrep
        self._original_step = original_step
        pyrep.step = step_with_capture

    def restore(self):
        if self._pyrep is not None and self._original_step is not None:
            self._pyrep.step = self._original_step
        self._pyrep = None
        self._original_step = None


def create_video_environment(
    resolution=(320, 240),
    *,
    front_image_plane_shift=(0.0, 0.0),
):
    import rlbench.environment as environment_module

    environment = environment_module.Environment(
        action_mode=make_action_mode(),
        obs_config=observation_config(("front", "overhead"), resolution),
        headless=True,
    )
    restore = prepare_headless_scene(environment_module)
    original_launch = environment.launch

    def launch():
        result = original_launch()
        shift = np.asarray(front_image_plane_shift, dtype=np.float64)
        if not np.allclose(shift, 0.0):
            sensor = environment._scene.camera_sensors["front"]
            transform = np.asarray(sensor.get_matrix(), dtype=np.float64)
            transform[:3, 3] += transform[:3, :3] @ np.array(
                [shift[0], shift[1], 0.0], dtype=np.float64
            )
            sensor.set_matrix(transform)
        return result

    environment.launch = launch
    return environment, restore


__all__ = [
    "FFmpegWriter",
    "ObservationRecorder",
    "PhysicsStepCapture",
    "create_video_environment",
    "observation_config",
]
