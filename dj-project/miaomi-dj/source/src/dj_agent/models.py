from dataclasses import asdict, dataclass, field


@dataclass
class Track:
    id: str
    path: str
    title: str
    artist: str
    duration: float
    sample_rate: int
    bpm: float
    beats: list[float]
    downbeats: list[float]
    key: str
    energy: float
    segments: list[dict]
    analysis_backend: str
    warnings: list[str] = field(default_factory=list)
    source_hash: str = ""
    beat_confidence: str = "unknown"

    def to_dict(self) -> dict:
        return asdict(self)
