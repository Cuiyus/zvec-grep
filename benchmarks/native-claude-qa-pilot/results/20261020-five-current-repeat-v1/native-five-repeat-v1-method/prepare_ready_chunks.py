"""Prepare disjoint anonymous chunks by artifact availability, never by outcome."""
import importlib.util,json
from pathlib import Path
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('_fixed_five_packets',HERE/'build_blind_packets.py')
packets=importlib.util.module_from_spec(spec);spec.loader.exec_module(packets)
for task in packets.TASKS:
 available=2*sum((packets.CI/f'{task}-r{i:02d}'/'artifact-byte-verification.json').is_file() for i in range(1,11))
 existing=list((packets.G/'chunks'/task).glob('*/manifest.json'))
 rows=sum(json.loads(p.read_text())['planned_answers'] for p in existing)
 assert 0<=rows<=available<=20
 if available-rows>=4 or available==20 and available>rows:packets.build(task,chunk=True)
 if available==20 and not (packets.G/task/'manifest.json').exists():packets.build(task)
