"""Generate static/openui-demo.js — pre-baked OpenUI Lang for headless screenshot."""
import json
from pathlib import Path

BASE = Path(__file__).parent
SEVERITY_ORDER = {'CRITICAL': 0, 'HIGH': 1, 'MEDIUM': 2, 'LOW': 3}


def risk_to_lang(risk):
    ws        = risk.get('weather_snapshot', {})
    wind      = ws.get('wind_knots_max_72h', 0)
    storm     = ws.get('storm_probability',  0)
    wave      = ws.get('wave_height_m',      0)
    eta_hours = risk.get('eta_impact_hrs', 0)
    reasoning = (risk.get('reasoning') or '').strip()
    alt_route = (risk.get('alternate_route') or 'null').strip()

    attrs = (
        f'vessel="{risk["vessel"]}" severity="{risk["severity"]}" '
        f'origin="{risk["origin"]}" destination="{risk["destination"]}" '
        f'cargo="{risk["cargo"]}" status="{risk["status"]}" '
        f'wind="{wind}" storm="{storm}" wave="{wave}" eta_hours="{eta_hours}"'
    )
    return f'<RiskCard {attrs}>\n{reasoning}\n||\n{alt_route}\n</RiskCard>'


risks = json.loads((BASE / 'mocks' / 'llm_decision.json').read_text(encoding='utf-8'))
risks.sort(key=lambda r: SEVERITY_ORDER.get(r.get('severity', 'LOW'), 99))
src = '\n\n'.join(risk_to_lang(r) for r in risks)

# Escape backticks and template-literal interpolations for JS template literal
src_escaped = src.replace('\\', '\\\\').replace('`', r'\`').replace('${', r'\${')

js = (
    '// Pre-generated OpenUI Lang source — used by demoStream() for headless screenshot\n'
    f'window.__OPENUI_DEMO_SOURCE__ = `{src_escaped}`;\n'
)
out = BASE / 'static' / 'openui-demo.js'
out.write_text(js, encoding='utf-8')
print(f'Written {len(src)} chars to {out}')
print('First 200 chars of source:')
print(src[:200])
