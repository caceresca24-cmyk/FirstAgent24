"""Local stdio MCP interface; register this with an MCP-capable agent."""
import json, sys
import app
TOOLS=[
 dict(name='get_search_plan',description='Read BNE-DPS search windows and filtering rules. Use your own authorised Trip.com connection to retrieve data.',inputSchema=dict(type='object',properties={},additionalProperties=False)),
 dict(name='get_flight_dashboard',description='Read stored fares, history, alerts, provider status and coverage.',inputSchema=dict(type='object',properties={},additionalProperties=False)),
 dict(name='import_trip_result',description='Persist an actual structured Trip.com one-way economy response. Detect price drops. Do not fabricate observations.',inputSchema=dict(type='object',properties=dict(result=dict(type='object')),required=['result'],additionalProperties=False)),
 dict(name='generate_flight_report',description='Save and return a top-five report from stored observations. Does not send messages or book travel.',inputSchema=dict(type='object',properties={},additionalProperties=False))]
def dispatch(method,p):
    if method=='initialize': return dict(protocolVersion='2024-11-05',capabilities=dict(tools=dict(listChanged=False)),serverInfo=dict(name='bali-flight-agent',version='1.0.0'))
    if method=='ping': return {}
    if method=='tools/list': return dict(tools=TOOLS)
    if method=='tools/call':
        args=p.get('arguments',{})
        try:
            name=p['name']
            if name=='get_search_plan': result=app.plan()
            elif name=='get_flight_dashboard': result=app.state()
            elif name=='import_trip_result': result=app.ingest(args['result'])
            elif name=='generate_flight_report': result=app.report()
            else: raise ValueError('Unknown tool')
            return dict(content=[dict(type='text',text=json.dumps(result))],isError=False)
        except (ValueError,KeyError,TypeError,IndexError,AttributeError) as e:
            return dict(content=[dict(type='text',text=str(e))],isError=True)
    raise ValueError('Unsupported method')
def main():
    for line in sys.stdin:
        if len(line)>2_000_000: continue
        request={}
        try:
            request=json.loads(line)
            if 'id' not in request: continue
            result=dispatch(request['method'],request.get('params',{}))
            response=dict(jsonrpc='2.0',id=request['id'],result=result)
        except Exception as e:
            response=dict(jsonrpc='2.0',id=request.get('id'),error=dict(code=-32602,message=str(e)))
        print(json.dumps(response),flush=True)
if __name__=='__main__': main()
