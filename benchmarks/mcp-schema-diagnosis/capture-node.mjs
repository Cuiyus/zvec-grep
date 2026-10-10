import { mkdir, writeFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
import { createRequire } from 'node:module';
const [packageRoot, output] = process.argv.slice(2);
const require = createRequire(`${packageRoot}/package.json`);
const { Client, InMemoryTransport } = await import(pathToFileURL(require.resolve('@modelcontextprotocol/client')));
const { createZvecGrepMcpServer } = await import(pathToFileURL(`${packageRoot}/dist/mcp/tools.js`));
const server = createZvecGrepMcpServer({}, 'schema-diagnosis', {toolset: 'agent'});
const client = new Client({name:'schema-diagnosis',version:'1'});
const [ct, st] = InMemoryTransport.createLinkedPair();
await Promise.all([client.connect(ct), server.connect(st)]);
await mkdir(output, {recursive:true});
await writeFile(`${output}/node-tools.json`, JSON.stringify({instructions:client.getInstructions(),...await client.listTools()}, null, 2));
const checks=[];
for (const values of [{limit:'15'}, {fuse:'true'}, {limit:15,fuse:true}]) {
  const args={root:output,query:'needle',...values};
  try {checks.push({args,result:await client.callTool({name:'zvec_grep_search',arguments:args})});}
  catch (error) {checks.push({args,error:String(error)});}
}
await writeFile(`${output}/node-parsing.json`, JSON.stringify(checks,null,2));
await client.close();
await server.close();
