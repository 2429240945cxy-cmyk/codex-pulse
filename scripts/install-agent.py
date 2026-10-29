#!/usr/bin/env python3
"""Install this checkout's collector as a per-user macOS LaunchAgent."""
import argparse,json,os,plistlib,shutil,subprocess,sys
from pathlib import Path
LABEL='dev.codexpulse.collector'
def main():
 p=argparse.ArgumentParser();p.add_argument('--config',type=Path);p.add_argument('--uninstall',action='store_true');args=p.parse_args()
 if sys.platform!='darwin':p.error('LaunchAgent installation requires macOS. Run collector.py directly on other platforms.')
 runtime=Path.home()/'Library/Application Support/CodexPulseOpenSource'
 plist=Path.home()/'Library/LaunchAgents'/f'{LABEL}.plist';domain=f'gui/{os.getuid()}'
 if args.uninstall:
  subprocess.run(['launchctl','bootout',f'{domain}/{LABEL}'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  plist.unlink(missing_ok=True);print('Collector stopped. Local configuration and history were preserved.');return
 if not args.config:p.error('--config is required')
 conf=json.loads(args.config.expanduser().read_text())
 if not conf.get('url') or not conf.get('ingest_token'):p.error('Config requires url and ingest_token')
 runtime.mkdir(parents=True,exist_ok=True);runtime.chmod(0o700)
 for f in (Path(__file__).resolve().parents[1]/'collector').glob('*'):
  if f.suffix=='.py' or f.name=='credit-seed.json':shutil.copy2(f,runtime/f.name)
 config=runtime/'config.json';config.write_text(json.dumps(conf,indent=2)+'\n');config.chmod(0o600)
 data={'Label':LABEL,'ProgramArguments':[sys.executable,str(runtime/'collector.py'),'--config',str(config)],'RunAtLoad':True,'KeepAlive':True,'ThrottleInterval':10,'ProcessType':'Background','StandardOutPath':str(runtime/'service.log'),'StandardErrorPath':str(runtime/'service.log')}
 plist.parent.mkdir(parents=True,exist_ok=True);plist.write_bytes(plistlib.dumps(data));plist.chmod(0o600)
 subprocess.run(['launchctl','bootout',f'{domain}/{LABEL}'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 result=subprocess.run(['launchctl','bootstrap',domain,str(plist)])
 if result.returncode:raise SystemExit('Service registration failed. Retry after the previous service stops.')
 print('Collector installed. It starts at login and retries failed uploads automatically.')
if __name__=='__main__':main()
