"""RLBench-only adapters for the environment-neutral LiteTwin policy."""

__all__ = ["LiteTwinObservationAdapter"]


def __getattr__(name):

    if name == "LiteTwinObservationAdapter":
        from .observation_adapter import LiteTwinObservationAdapter

        return LiteTwinObservationAdapter
    raise AttributeError(name)
