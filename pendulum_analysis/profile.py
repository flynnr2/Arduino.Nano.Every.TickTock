"""Clock profile and capability detection."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

from .yamlutil import load_yaml


@dataclass(frozen=True)
class ProfilePhase:
    primary_modulus: Optional[int]
    expected_moduli: List[int]
    event_unit: str
    description: str


@dataclass(frozen=True)
class ProfileAnnotation:
    id: str
    label: str
    period_s: Optional[float]
    frequency_hz: Optional[float]
    enabled_by_capability: Optional[str] = None


@dataclass(frozen=True)
class ExpectedPeriod:
    period_s: float
    frequency_hz: float
    label: str
    family: str
    order: Optional[int]
    source: str
    confidence: str = "configured"

    @classmethod
    def from_period(
        cls,
        period_s: float,
        label: str,
        family: str = "configured",
        order: Optional[int] = None,
        source: str = "user_config",
        confidence: str = "configured",
    ) -> "ExpectedPeriod":
        return cls(
            period_s=float(period_s),
            frequency_hz=float(1.0 / period_s),
            label=label,
            family=family,
            order=order,
            source=source,
            confidence=confidence,
        )


@dataclass(frozen=True)
class ClockProfile:
    name: str
    display_name: str
    description: str
    nominal_period_s: Optional[float]
    nominal_half_period_s: Optional[float]
    cycle_period_s: Optional[float]
    cycle_events: Optional[int]
    impulse_cycle_events: Optional[int]
    phase: ProfilePhase
    expected_phase_moduli: List[int]
    expected_structure_periods_s: List[ExpectedPeriod]
    annotations_fft: List[ProfileAnnotation]
    capabilities: Dict[str, bool]
    supports_tick_tock: bool
    supports_block_intervals: bool
    supports_impulse_cycle: bool
    supports_one_sided_impulse: bool
    notes: str
    source_path: Optional[str] = None
    raw: Dict[str, Any] | None = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def supports(self, capability: str) -> bool:
        return bool(self.capabilities.get(capability, False))

    def expected_harmonic_family(
        self,
        base_period_s: float,
        max_order: int,
        min_period_s: Optional[float] = None,
        max_period_s: Optional[float] = None,
        family: str = "configured",
        family_label: Optional[str] = None,
        source: str = "clock_profile",
    ) -> List[ExpectedPeriod]:
        periods: List[ExpectedPeriod] = []
        for order in range(1, int(max_order) + 1):
            period = float(base_period_s) / float(order)
            if min_period_s is not None and period < min_period_s:
                continue
            if max_period_s is not None and period > max_period_s:
                continue
            periods.append(
                ExpectedPeriod.from_period(
                    period,
                    str(family_label) if order == 1 and family_label else _harmonic_label(order),
                    family=family,
                    order=order,
                    source=source,
                )
            )
        return periods

    @classmethod
    def from_yaml_dict(cls, data: Dict[str, Any], *, source_path: Optional[Path] = None) -> "ClockProfile":
        validate_profile_dict(data)
        capabilities = data.get("capabilities", {})
        phase_data = data.get("phase") or {}
        old_moduli = [int(value) for value in data.get("expected_phase_moduli", [])]
        expected_moduli = [int(value) for value in phase_data.get("expected_moduli", old_moduli)]
        primary_modulus = _optional_int(phase_data.get("primary_modulus"))
        if primary_modulus is None and expected_moduli:
            primary_modulus = expected_moduli[0]
        phase = ProfilePhase(
            primary_modulus=primary_modulus,
            expected_moduli=expected_moduli,
            event_unit=str(phase_data.get("event_unit", "event")),
            description=str(phase_data.get("description", "")),
        )
        normalized_capabilities = {str(key): bool(value) for key, value in capabilities.items()}
        profile = cls(
            name=str(data["id"]),
            display_name=str(data["display_name"]),
            description=str(data.get("description", data.get("notes", ""))),
            nominal_period_s=_optional_float(data.get("nominal_period_s")),
            nominal_half_period_s=_optional_float(data.get("nominal_half_period_s")),
            cycle_period_s=_optional_float(data.get("cycle_period_s")),
            cycle_events=_optional_int(data.get("cycle_events")),
            impulse_cycle_events=_optional_int(data.get("impulse_cycle_events", data.get("cycle_events"))),
            phase=phase,
            expected_phase_moduli=expected_moduli,
            expected_structure_periods_s=[],
            annotations_fft=[],
            capabilities=normalized_capabilities,
            supports_tick_tock=bool(normalized_capabilities.get("tick_tock", False)),
            supports_block_intervals=bool(normalized_capabilities.get("block_intervals", False)),
            supports_impulse_cycle=bool(normalized_capabilities.get("impulse_cycle", False)),
            supports_one_sided_impulse=bool(normalized_capabilities.get("one_sided_impulse", False)),
            notes=str(data.get("notes", "")),
            source_path=str(source_path) if source_path is not None else None,
            raw=dict(data),
        )
        expected: List[ExpectedPeriod] = []
        for family in data.get("harmonic_families", []):
            if family.get("enabled_by_capability") and not profile.supports(str(family["enabled_by_capability"])):
                continue
            base = float(family["base_period_s"])
            max_order = int(family.get("max_order", 12))
            expected.extend(
                profile.expected_harmonic_family(
                    base,
                    max_order=max_order,
                    min_period_s=_optional_float(family.get("min_period_s")),
                    max_period_s=_optional_float(family.get("max_period_s")),
                    family=str(family.get("id", family.get("name", "configured"))),
                    family_label=str(family["label"]) if family.get("label") else None,
                    source="clock_profile",
                )
            )
        annotations: List[ProfileAnnotation] = []
        for item in (data.get("annotations") or {}).get("fft", []):
            capability = item.get("enabled_by_capability")
            if capability and not profile.supports(str(capability)):
                continue
            period_s = _optional_float(item.get("period_s"))
            frequency_hz = _optional_float(item.get("frequency_hz"))
            if frequency_hz is None and period_s:
                frequency_hz = 1.0 / period_s
            if period_s is None and frequency_hz:
                period_s = 1.0 / frequency_hz
            annotations.append(
                ProfileAnnotation(
                    id=str(item.get("id", item.get("label", "annotation"))),
                    label=str(item.get("label", item.get("id", "Annotation"))),
                    period_s=period_s,
                    frequency_hz=frequency_hz,
                    enabled_by_capability=str(capability) if capability else None,
                )
            )
        object.__setattr__(profile, "expected_structure_periods_s", expected)
        object.__setattr__(profile, "annotations_fft", annotations)
        return profile


@dataclass(frozen=True)
class ProfileSelection:
    requested: str
    selected: ClockProfile
    mode: str
    inferred_nominal_period_s: Optional[float]
    detected_cycle_events: Optional[int]
    confidence: str
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["selected"] = self.selected.to_dict()
        return payload


@dataclass(frozen=True)
class AnalysisCapabilities:
    supports_tick_tock_analysis: bool
    supports_block_analysis: bool
    supports_phase_fold_analysis: bool
    supports_impulse_cycle_interpretation: bool
    supports_expected_harmonic_labels: bool
    supports_environmental_analysis: bool
    supports_timebase_health: bool
    supports_long_run_analysis: bool
    reasons: Dict[str, str]

    def enabled(self) -> List[str]:
        return [key for key, value in asdict(self).items() if key.startswith("supports_") and value is True]

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["enabled"] = self.enabled()
        return payload


ENV_COLUMNS = {"temp_C", "temperature_C", "humidity_pct", "pressure_hPa"}
BLOCK_COLUMNS = {"open_A_s", "block_A_s", "open_B_s", "block_B_s", "open_A", "block_A", "open_B", "block_B"}
TICK_TOCK_COLUMNS = {"half_A_s", "half_B_s", "half_asymmetry_ms", "tick", "tock"}
PCPS_COLUMNS = {"pps_t_s", "pps_raw_error_ns", "valid_pps_interval", "cap16", "latency16"}


def _harmonic_label(order: int) -> str:
    if order == 1:
        return "fundamental"
    if order == 2:
        return "2nd harmonic"
    if order == 3:
        return "3rd harmonic"
    return f"{order}th harmonic"


def builtin_profile_dir() -> Path:
    packaged = Path(__file__).resolve().parent / "profiles"
    return packaged if packaged.exists() else Path(__file__).resolve().parents[1] / "profiles"


def _optional_float(value: Any) -> Optional[float]:
    return None if value is None else float(value)


def _optional_int(value: Any) -> Optional[int]:
    return None if value is None else int(value)


def load_profile_file(path: Path) -> ClockProfile:
    return ClockProfile.from_yaml_dict(load_yaml(path), source_path=path)


def validate_profile_dict(data: Dict[str, Any]) -> None:
    required = ["id", "display_name", "capabilities"]
    missing = [key for key in required if key not in data]
    if missing:
        raise ValueError(f"clock profile missing required field(s): {', '.join(missing)}")
    if "expected_phase_moduli" in data and not isinstance(data["expected_phase_moduli"], list):
        raise ValueError("clock profile expected_phase_moduli must be a list")
    if "phase" in data and not isinstance(data["phase"], dict):
        raise ValueError("clock profile phase must be a mapping")
    if "harmonic_families" in data and not isinstance(data["harmonic_families"], list):
        raise ValueError("clock profile harmonic_families must be a list")
    if not isinstance(data["capabilities"], dict):
        raise ValueError("clock profile capabilities must be a mapping")
    for key in ["nominal_period_s", "nominal_half_period_s", "cycle_period_s"]:
        value = data.get(key)
        if value is not None and float(value) <= 0:
            raise ValueError(f"clock profile {key} must be positive when supplied")
    for family in data.get("harmonic_families", []):
        if "base_period_s" not in family:
            raise ValueError("clock profile harmonic family missing base_period_s")
        if float(family["base_period_s"]) <= 0:
            raise ValueError("clock profile harmonic family base_period_s must be positive")


def load_profile(name: str, *, profile_file: Optional[Path] = None, profile_dir: Optional[Path] = None) -> ClockProfile:
    if profile_file is not None:
        return load_profile_file(profile_file)
    search_dirs = []
    if profile_dir is not None:
        search_dirs.append(profile_dir)
    project_dir = Path.cwd() / "profiles"
    if project_dir.exists():
        search_dirs.append(project_dir)
    search_dirs.append(builtin_profile_dir())
    for directory in search_dirs:
        path = directory / f"{name}.yaml"
        if path.exists():
            return load_profile_file(path)
    raise ValueError(f"unknown clock profile: {name}")


def available_profiles(profile_dir: Optional[Path] = None) -> Dict[str, ClockProfile]:
    profiles: Dict[str, ClockProfile] = {}
    for directory in [builtin_profile_dir(), *( [profile_dir] if profile_dir is not None else [] )]:
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.yaml")):
            profile = load_profile_file(path)
            profiles[profile.name] = profile
    return profiles


PROFILES = available_profiles()
GENERIC_PROFILE = PROFILES["generic"]
SYNCHRONOME_PROFILE = PROFILES["synchronome"]


def get_profile(name: str, *, profile_file: Optional[Path] = None, profile_dir: Optional[Path] = None) -> ClockProfile:
    return load_profile(name, profile_file=profile_file, profile_dir=profile_dir)


def select_clock_profile(
    requested: str,
    frame: pd.DataFrame,
    *,
    profile_file: Optional[Path] = None,
    profile_dir: Optional[Path] = None,
) -> ProfileSelection:
    inferred = infer_nominal_period(frame)
    cycle, cycle_reason = detect_cycle_like_structure(frame)
    if requested == "auto":
        profile = get_profile("generic", profile_dir=profile_dir)
        reason = "auto mode uses generic analysis unless a clock family is explicitly configured"
        if cycle is not None:
            reason = f"{reason}; {cycle_reason}"
        return ProfileSelection(
            requested=requested,
            selected=profile,
            mode="auto-conservative",
            inferred_nominal_period_s=inferred,
            detected_cycle_events=cycle,
            confidence="suggested" if cycle is not None else "low",
            reason=reason,
        )
    profile = get_profile(requested, profile_file=profile_file, profile_dir=profile_dir)
    mode = "explicit" if requested != "generic" else "generic"
    return ProfileSelection(
        requested=requested,
        selected=profile,
        mode=mode,
        inferred_nominal_period_s=inferred,
        detected_cycle_events=cycle,
        confidence="configured",
        reason=f"profile selected by configuration: {requested}",
    )


def infer_nominal_period(frame: pd.DataFrame) -> Optional[float]:
    for column in ["full_s", "period_s"]:
        if column in frame.columns:
            values = pd.to_numeric(frame[column], errors="coerce")
            values = values[np.isfinite(values)]
            if not values.empty:
                return float(values.median())
    return None


def detect_cycle_like_structure(frame: pd.DataFrame, candidates: Optional[Iterable[int]] = None) -> tuple[Optional[int], str]:
    if "full_resid_us" not in frame.columns or len(frame) < 60:
        return None, "no sufficient residual stream for cycle detection"
    if candidates is None:
        candidates = range(2, min(61, max(3, len(frame) // 2)))
    source = frame["seq"] if "seq" in frame.columns else pd.Series(range(len(frame)), index=frame.index)
    values = pd.to_numeric(frame["full_resid_us"], errors="coerce")
    best_cycle: Optional[int] = None
    best_score = 0.0
    for modulus in candidates:
        phase = pd.to_numeric(source, errors="coerce").fillna(0).astype(int) % int(modulus)
        med = values.groupby(phase).median()
        if len(med.dropna()) < max(3, int(modulus) // 2):
            continue
        spread = float(med.max() - med.min())
        noise = float(values.std(ddof=1)) if values.notna().sum() > 1 else 0.0
        score = spread / noise if noise > 0 else 0.0
        if score > best_score:
            best_cycle = int(modulus)
            best_score = score
    if best_cycle is not None and best_score >= 0.25:
        return best_cycle, f"detected cycle-like structure at approximately {best_cycle} events"
    return None, "no strong cycle-like structure detected"


def derive_capabilities(profile: ClockProfile, frame: pd.DataFrame, pcps: pd.DataFrame, duration_days: float) -> AnalysisCapabilities:
    columns = set(frame.columns)
    pcps_columns = set(pcps.columns)
    reasons: Dict[str, str] = {}

    tick_tock_columns = bool(columns & TICK_TOCK_COLUMNS)
    block_columns = bool(columns & BLOCK_COLUMNS)
    env_columns = bool(columns & ENV_COLUMNS)
    pcps_available = bool(pcps_columns & PCPS_COLUMNS) and not pcps.empty

    tick_tock = tick_tock_columns and profile.supports_tick_tock
    block = block_columns and profile.supports_block_intervals
    phase = bool(profile.phase.expected_moduli)
    impulse = phase and profile.supports_impulse_cycle
    harmonics = bool(profile.expected_structure_periods_s)

    if not tick_tock:
        reasons["supports_tick_tock_analysis"] = "alternating tick/tock fields unavailable" if not tick_tock_columns else "profile does not define tick/tock semantics"
    if not block:
        reasons["supports_block_analysis"] = "block interval columns unavailable" if not block_columns else "profile does not define block interval semantics"
    if not phase:
        reasons["supports_phase_fold_analysis"] = "no configured or detected cycle model"
    if not impulse:
        reasons["supports_impulse_cycle_interpretation"] = "impulse-cycle interpretation requires an explicit supporting profile"
    if not harmonics:
        reasons["supports_expected_harmonic_labels"] = "no expected structure periods configured"
    if not env_columns:
        reasons["supports_environmental_analysis"] = "environmental columns unavailable"
    if not pcps_available:
        reasons["supports_timebase_health"] = "PCPS/CPS columns unavailable"
    if duration_days < 7.0:
        reasons["supports_long_run_analysis"] = "dataset is shorter than medium-run threshold"

    return AnalysisCapabilities(
        supports_tick_tock_analysis=tick_tock,
        supports_block_analysis=block,
        supports_phase_fold_analysis=phase,
        supports_impulse_cycle_interpretation=impulse,
        supports_expected_harmonic_labels=harmonics,
        supports_environmental_analysis=env_columns,
        supports_timebase_health=pcps_available,
        supports_long_run_analysis=duration_days >= 7.0,
        reasons=reasons,
    )
