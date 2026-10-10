import copy
import json
from pathlib import Path
import tempfile
import unittest
from diagnose import build_arms,request_body,wrong_types,parse_rpc_body,representation_arms,assemble_stream


class DiagnosticTest(unittest.TestCase):
 def test_streamed_argument_fragments_preserve_types(self):
  chunks=[{'model':'qwen3.8-max','choices':[{'index':0,'delta':{'tool_calls':[{'index':0,'id':'call_1','function':{'name':'search','arguments':'{"limit":'}}]}}]},
          {'choices':[{'index':0,'delta':{'tool_calls':[{'index':0,'function':{'arguments':'15,"fuse":true}'}}]},'finish_reason':'tool_calls'}]},
          {'choices':[],'usage':{'prompt_tokens':10,'completion_tokens':20}}]
  raw=''.join('data: '+json.dumps(c)+'\n\n' for c in chunks)+'data: [DONE]\n\n'
  result=assemble_stream(raw);c=result['choices'][0]
  self.assertEqual(c['message']['tool_calls'][0]['function']['name'],'search')
  self.assertEqual(json.loads(c['message']['tool_calls'][0]['function']['arguments']),{'limit':15,'fuse':True})
  self.assertEqual(result['usage']['completion_tokens'],20)
  self.assertEqual(c['finish_reason'],'tool_calls')

 def test_nullable_and_array_not_confounded(self):
  original={'name':'zvec_grep_search','inputSchema':{'properties':{'limit':{'type':['integer','null'],'minimum':1},'fuse':{'type':['boolean','null']}}}}
  with tempfile.TemporaryDirectory() as directory:
   p=Path(directory);(p/'rust-tools.json').write_text(json.dumps({'tools':[original]}))
   arms=representation_arms(p)
   self.assertEqual(arms['rust-nullable-array'],original)
   self.assertEqual(arms['rust-singleton-array']['inputSchema']['properties']['limit']['type'],['integer'])
   anyof=arms['rust-nullable-anyof']['inputSchema']['properties']['limit']
   self.assertEqual(anyof,{'minimum':1,'anyOf':[{'type':'integer'},{'type':'null'}]})
   self.assertEqual(arms['rust-scalar']['inputSchema']['properties']['limit']['type'],'integer')

 def test_streamable_http_empty_priming_event(self):
  body='event: message\r\ndata: \r\n\r\nevent: message\r\ndata: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\r\n\r\n'
  self.assertEqual(parse_rpc_body(body,1)['result'],{'ok':True})
  self.assertIsNone(parse_rpc_body('',None))

 def test_only_two_type_keywords_change(self):
  originals={}
  with tempfile.TemporaryDirectory() as directory:
   p=Path(directory)
   for runtime in ['node','rust']:
    t={'name':'zvec_grep_search','description':runtime,'inputSchema':{'type':'object','required':['root'],'properties':{'root':{'type':'string'},'limit':{'type':'integer','maximum':50},'fuse':{'type':'boolean','description':'original field text'}}}}
    if runtime=='rust':
     for k in ['limit','fuse']:t['inputSchema']['properties'][k]['type']=[t['inputSchema']['properties'][k]['type'],'null']
    originals[runtime]=copy.deepcopy(t)
    (p/f'{runtime}-tools.json').write_text(json.dumps({'tools':[t]}))
   arms=build_arms(p)
   for runtime,donor,label in [('node','rust','node-rust-types'),('rust','node','rust-node-types')]:
    expected=copy.deepcopy(originals[runtime])
    for k in ['limit','fuse']:expected['inputSchema']['properties'][k]['type']=originals[donor]['inputSchema']['properties'][k]['type']
    self.assertEqual(expected,arms[label]);self.assertEqual(originals[runtime],arms[f'{runtime}-original'])
   bodies=[request_body(v,1) for v in arms.values()]
   for b in bodies:b.pop('tools')
   self.assertTrue(all(b==bodies[0] for b in bodies))

 def test_type_detection_does_not_coerce(self):
  self.assertEqual(wrong_types({'limit':'15','fuse':'true'}),{'limit':'str','fuse':'str'})
  self.assertEqual(wrong_types({'limit':15,'fuse':True}),{})
  self.assertEqual(wrong_types({'limit':True}),{'limit':'bool'})
  self.assertEqual(wrong_types({'limit':None,'fuse':None}),{})
  self.assertEqual(wrong_types({}),{})


if __name__=='__main__':unittest.main()
