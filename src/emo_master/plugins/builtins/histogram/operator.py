from dataclasses import replace

from emo_master.plugins.builtins._intensity_operators import (
    HistogramOperator as _HistogramOperator,
)


class HistogramOperator(_HistogramOperator):
    meta = replace(_HistogramOperator.meta, version="1.1.0")
