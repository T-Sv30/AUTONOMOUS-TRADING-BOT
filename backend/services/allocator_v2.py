from typing import Any


class AllocatorV2:
    def rank(self, signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            signals,
            key=lambda s: (abs(float(s.get("score", 0.0))), float(s.get("confidence", 0.0))),
            reverse=True,
        )

    def allocate(self, signals: list[dict[str, Any]], base_notional: float = 100.0) -> dict[str, Any]:
        ranked = self.rank(signals)
        if not ranked:
            return {"action": "HOLD", "notional": 0.0, "selected": None, "candidates": []}

        top = ranked[0]
        action = top.get("action", "HOLD")
        confidence = float(top.get("confidence", 50.0)) / 100.0
        score = abs(float(top.get("score", 0.0)))

        scale = min(1.5, max(0.5, 0.5 + score * 0.25))
        notional = base_notional * confidence * scale

        if action == "HOLD":
            return {"action": "HOLD", "notional": 0.0, "selected": top, "candidates": ranked[:3]}

        return {
            "action": action,
            "notional": round(notional, 8),
            "selected": top,
            "candidates": ranked[:3],
        }
