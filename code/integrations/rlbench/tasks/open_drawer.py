"""Expose task-relevant Open Drawer state for object-centric control."""

from __future__ import annotations

import numpy as np
from pyrep.objects.shape import Shape
from rlbench.tasks.open_drawer import OpenDrawer


class LiteTwinOpenDrawer(OpenDrawer):

    def __init__(self, pyrep, robot):
        super().__init__(pyrep, robot, name="open_drawer")

    def get_low_dim_state(self) -> np.ndarray:
        option = self._options[self._current_index]
        panel = Shape(f"drawer_{option}")
        joint = self._joints[self._current_index]
        return np.concatenate(
            (
                np.asarray(panel.get_pose(), dtype=np.float64),
                np.asarray(joint.get_pose(), dtype=np.float64),
                np.asarray([joint.get_joint_position()], dtype=np.float64),
            )
        )


__all__ = ["LiteTwinOpenDrawer"]
