import json, os, shutil, subprocess, sys, tempfile, time, unittest
from pathlib import Path

HERE=Path(__file__).parent
BINARY=json.loads((HERE/"installation.json").read_text())["binary_path"]
OriginalTemporaryDirectory=tempfile.TemporaryDirectory
captures=[]
class CaptureDirectory(OriginalTemporaryDirectory):
    def cleanup(self):
        root=Path(self.name).resolve()
        marker=root/"agent/session-usage.json"
        if marker.exists() and not getattr(self,"_captured",False):
            self._captured=True
            usage=json.loads(marker.read_text())
            config=json.loads((root/"opencode.json").read_text())
            destination=HERE/"fixtures"/f"case-{len(captures)+1:02d}"
            destination.mkdir(parents=True,exist_ok=True)
            shutil.copytree(root/"agent",destination/"agent")
            shutil.copy2(root/"opencode.json",destination/"opencode.json")
            env={"PATH":os.environ["PATH"],"HOME":str(root),"LANG":"en_US.UTF-8","OPENCODE_CONFIG":str(root/"opencode.json"),"OPENCODE_DISABLE_MODELS_FETCH":"true","OPENCODE_DISABLE_PROJECT_CONFIG":"true","OPENCODE_DISABLE_EXTERNAL_SKILLS":"true","XDG_CONFIG_HOME":str(root/"config"),"XDG_DATA_HOME":str(root/"data"),"XDG_STATE_HOME":str(root/"state"),"XDG_CACHE_HOME":str(root/"cache"),"OPENAI_API_KEY":"offline-fixture-not-a-real-key"}
            exports=[]
            for session in usage["sessions"]:
                sid=session["session_id"]
                command=[BINARY,"export",sid]
                result=subprocess.run(command,env=env,cwd=root/"corpus",capture_output=True,text=True,timeout=30)
                (destination/f"{sid}.export.json").write_text(result.stdout)
                (destination/f"{sid}.export.stderr").write_text(result.stderr)
                exports.append({"session_id":sid,"argv":command,"returncode":result.returncode})
                if result.returncode!=0:raise RuntimeError(f"offline export failed: {sid}: {result.stderr}")
                json.loads(result.stdout)
            entry={"destination":str(destination),"config_model":config.get("model"),"provider_ids":list(config["provider"]),"root_session_id":usage["root_session_id"],"session_ids":[s["session_id"] for s in usage["sessions"]],"exports":exports}
            (destination/"capture-manifest.json").write_text(json.dumps(entry,indent=2)+"\n")
            captures.append(entry)
        super().cleanup()
tempfile.TemporaryDirectory=CaptureDirectory
started=time.time()
suite=unittest.defaultTestLoader.loadTestsFromNames(["test_opencode.OpenCodeSamplingContractTests"])
result=unittest.TextTestRunner(verbosity=2).run(suite)
report={"started_at_unix":started,"finished_at_unix":time.time(),"tests_run":result.testsRun,"success":result.wasSuccessful(),"failures":len(result.failures),"errors":len(result.errors),"skipped":len(result.skipped),"captures":captures,"note":"Supplemental rerun of unchanged native test; external TemporaryDirectory cleanup subclass copies fake fixtures and executes read-only export before original cleanup."}
(HERE/"fixture-capture-results.json").write_text(json.dumps(report,indent=2)+"\n")
sys.exit(0 if result.wasSuccessful() else 1)
