"""Sequential role-play queue runner for EnergyBridge benchmark."""
from __future__ import annotations
import traceback, dataclasses
from pathlib import Path
from typing import Callable, Any
from energybridge.roleplay.schema import to_legacy_dict


def run_roleplay_queue(
    personas: list[dict],
    run_fn: Callable[..., Any],
    *,
    cities: list[str] | None = None,
    method: str = "EnergyBridge",
    output_base_dir: Path | None = None,
    extra_kwargs: dict | None = None,
    verbose: bool = True,
) -> list[dict]:
    """Run each persona sequentially through run_fn.

    run_fn must accept keyword args: output_dir, weather_label, persona_name.
    Returns list of result dicts (one per persona x city).
    """
    if extra_kwargs is None:
        extra_kwargs = {}
    city_list = cities or ["default"]
    all_results: list[dict] = []
    total = len(personas) * len(city_list)
    n = 0

    for persona in personas:
        flat = to_legacy_dict(persona) if "preferences" in persona else persona
        persona_id = flat.get("id", "unknown")

        for city in city_list:
            n += 1
            if verbose:
                print(f"\n[{n}/{total}] persona={persona_id}  city={city}  method={method}")

            out_dir = None
            if output_base_dir is not None:
                out_dir = Path(output_base_dir) / persona_id / city
                out_dir.mkdir(parents=True, exist_ok=True)

            kw = dict(extra_kwargs)
            if out_dir is not None:
                kw["output_dir"] = out_dir
            kw["weather_label"] = city
            kw["persona_name"]  = persona_id

            try:
                result = run_fn(**kw)
                if hasattr(result, "__dataclass_fields__"):
                    rd_dict = dataclasses.asdict(result)
                elif isinstance(result, dict):
                    rd_dict = result
                else:
                    rd_dict = {"raw": str(result)}
                rd_dict.update({"persona_id": persona_id, "city": city, "method": method})
                all_results.append(rd_dict)
                if verbose:
                    print(f"  energy={rd_dict.get('energy_kwh_per_day','?')} kWh/day  "
                          f"pmv={rd_dict.get('pmv_ok_fraction',0)*100:.1f}%  "
                          f"score={rd_dict.get('user_pref_score','?')}")
            except Exception as exc:
                if verbose:
                    print(f"  ERROR: {exc}")
                    traceback.print_exc()
                all_results.append({"persona_id": persona_id, "city": city,
                                    "method": method, "error": str(exc)})

    if verbose:
        ok = [r for r in all_results if "error" not in r]
        errs = [r for r in all_results if "error" in r]
        print(f"\nQUEUE DONE: {len(ok)} ok, {len(errs)} errors")
        if ok:
            print(f"  {'Persona':<25} {'City':<12} {'Energy':>8} {'PMV%':>6} {'Score':>6}")
            for r in ok:
                print(f"  {r.get('persona_id','?'):<25} {r.get('city','?'):<12} "
                      f"{r.get('energy_kwh_per_day',0):>8.1f} "
                      f"{r.get('pmv_ok_fraction',0)*100:>6.1f} "
                      f"{r.get('user_pref_score',0):>6.2f}")
    return all_results
