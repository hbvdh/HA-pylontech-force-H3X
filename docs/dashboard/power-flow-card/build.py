"""Build the power flow card YAML files from card.js + the configs below.

Usage:  python build.py
Edit card.js (shared code) or a config below, then run this script and
commit the regenerated YAML files together with the change.
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
body = (HERE / "card.js").read_text(encoding="utf-8")
CONFIGS = {
 'power-flow-dtsu-ct2.yaml': {
   'netLabel': 'Net (DTSU)',
   'net': ['sensor.dtsu_direct_actief_vermogen_l1','sensor.dtsu_direct_actief_vermogen_l2','sensor.dtsu_direct_actief_vermogen_l3'],
   'netPhases': ['sensor.dtsu_direct_actief_vermogen_l1','sensor.dtsu_direct_actief_vermogen_l2','sensor.dtsu_direct_actief_vermogen_l3'],
   'schuur': ['sensor.dtsu_direct_ct2_actief_vermogen_l1','sensor.dtsu_direct_ct2_actief_vermogen_l2','sensor.dtsu_direct_ct2_actief_vermogen_l3'],
   'pv': 'sensor.pylontech_pv_power_watts',
   'pvStrings': [['PV1','sensor.kelder_pylontech_force_h3x_pv1_power'],['PV2','sensor.kelder_pylontech_force_h3x_pv2_power']],
   'ac': 'sensor.pylontech_force_h3x_ac_total_power',
   'ems': 'select.pylontech_force_h3x_ems_mode',
   'heat': 'sensor.pylontech_force_h3x_heatsink_temperature',
   'batP': 'sensor.pylontech_battery_power_watts',
   'soc': 'sensor.pylontech_force_h3x_battery_soc',
   'batV': 'sensor.pylontech_force_h3x_battery_voltage',
   'batA': 'sensor.pylontech_force_h3x_battery_current',
   'batKwh': 'sensor.batterij_resterend_kwh',
   'capacity': None,
   'huis': 'sensor.pylontech_load_power_watts',
   'idle': 25, 'netIdle': 100,
 },
 'power-flow-h3x-only.yaml': {
   'netLabel': 'Net (SDM630)',
   'net': ['sensor.pylontech_force_h3x_grid_total_power'],
   'netPhases': ['sensor.pylontech_force_h3x_grid_power_r','sensor.pylontech_force_h3x_grid_power_s','sensor.pylontech_force_h3x_grid_power_t'],
   'schuur': [],
   'pv': 'sensor.pylontech_force_h3x_pv_total_power',
   'pvStrings': [['PV1','sensor.pylontech_force_h3x_pv1_power']],
   'ac': 'sensor.pylontech_force_h3x_ac_total_power',
   'ems': 'select.pylontech_force_h3x_ems_mode',
   'heat': 'sensor.pylontech_force_h3x_heatsink_temperature',
   'batP': 'sensor.pylontech_force_h3x_battery_power',
   'soc': 'sensor.pylontech_force_h3x_battery_soc',
   'batV': 'sensor.pylontech_force_h3x_battery_voltage',
   'batA': 'sensor.pylontech_force_h3x_battery_current',
   'batKwh': None,
   'capacity': 10.24,
   'huis': 'sensor.pylontech_force_h3x_load_power',
   'idle': 25, 'netIdle': 100,
 },
}
COMMENTS = {
 'netLabel':  'titel van het netblok',
 'net':       'netvermogen (W, afname +); meerdere sensoren worden opgeteld',
 'netPhases': 'per fase getoond onder het netblok (leeg = niet tonen)',
 'schuur':    'AC-gekoppelde PV (leeg = blok weg, kaart wordt korter)',
 'pv':        'totaal PV-vermogen op de H3X (DC)',
 'pvStrings': 'strings onder het PV-blok: [label, sensor]',
 'batP':      'batterijvermogen (ontladen +)',
 'batKwh':    'resterende kWh-sensor; null = berekenen uit SoC x capacity',
 'capacity':  'batterijcapaciteit in kWh (alleen als batKwh null is)',
 'huis':      'totale load',
 'idle':      'W: daaronder lijn grijs en stil',
 'netIdle':   'W: drempel net (gelijk aan mushroom Net Status)',
}
for fname, cfg in CONFIGS.items():
    ents = []
    for k, v in cfg.items():
        if isinstance(v, str) and '.' in v and k != 'netLabel': ents.append(v)
        elif isinstance(v, list):
            for x in v:
                if isinstance(x, list): ents.append(x[1])
                elif isinstance(x, str): ents.append(x)
    seen = []
    [seen.append(e) for e in ents if e not in seen]
    cfglines = ['      // ---- Configuratie: alleen hier aanpassen ----', '      const CFG = {']
    for k, v in cfg.items():
        c = f'   // {COMMENTS[k]}' if k in COMMENTS else ''
        cfglines.append(f'        {k}: {json.dumps(v)},{c}')
    cfglines.append('      };')
    y = []
    y.append('type: custom:button-card')
    y += ['show_name: false','show_icon: false','show_state: false','tap_action:','  action: none',
          'grid_options:','  columns: full','  rows: auto','triggers_update:']
    y += [f'  - {e}' for e in seen]
    y += ['styles:','  card:','    - padding: 8px','  grid:',"    - grid-template-areas: '\"flow\"'",
          '    - grid-template-columns: 1fr','  custom_fields:','    flow:','      - width: 100%',
          'extra_styles: |',
          '  @keyframes pfflow { to { stroke-dashoffset: -12; } }',
          '  .pt  { font-size: 13px;   font-weight: 500; fill: var(--primary-text-color); }',
          '  .pb  { font-size: 24px;   fill: var(--primary-text-color); }',
          '  .pb2 { font-size: 30px;   font-weight: 500; fill: var(--primary-text-color); }',
          '  .ps  { font-size: 11.5px; fill: var(--secondary-text-color); }',
          'custom_fields:','  flow: |','    [[[']
    y += cfglines
    y.append('')
    y.append(body.rstrip('\n'))
    y.append('    ]]]')
    (HERE / fname).write_text('\n'.join(y) + '\n', encoding='utf-8')
    print(fname, len(seen), 'entities')
