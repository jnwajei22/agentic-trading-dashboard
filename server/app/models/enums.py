from enum import Enum

class Direction(str, Enum):
    long = "long"
    short = "short"

class SetupStatus(str, Enum):
    eligible_for_preview = "eligible_for_preview"
    watch = "watch"
    weak = "weak"
    no_setup = "no_setup"

class OrderPreviewStatus(str, Enum):
    preview_only = "preview_only"
    expired = "expired"
    submitted = "submitted"
    rejected = "rejected"

class Environment(str, Enum):
    paper = "paper"
    live = "live"
