from livekit.agents import Plugin

from .log import logger
from .tts import TTS
from .version import __version__

__all__ = ["TTS", "__version__"]


class TencentPlugin(Plugin):
    def __init__(self) -> None:
        super().__init__(__name__, __version__, __package__, logger)


Plugin.register_plugin(TencentPlugin())

_module = dir()
NOT_IN_ALL = [m for m in _module if m not in __all__]

__pdoc__: dict[str, bool] = {}
for n in NOT_IN_ALL:
    __pdoc__[n] = False
