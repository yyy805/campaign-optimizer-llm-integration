"""Deterministically export public rule cards into lightweight-RAG documents.

Each document is a faithful projection: a metadata header plus the verbatim
rule card JSON, so retrieval can never alter rule definitions. The manifest
records per-document checksums and the pinned ontology release identity so a
Bailian knowledge base publication can be tied to an exact release. Default
dry: prints the plan; pass --write to materialize documents.
"""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from campaign_optimizer.contracts.authority import RULES_DIR
from campaign_optimizer.llm.release_pin import load_verified_manifests,release_identity
def current_identity():
 manifests=load_verified_manifests()
 manifest=next(value for value in manifests.values() if value["ontology_version"]=="2.0-campaign-pending")
 return release_identity(manifest)
def export_documents():
 identity=current_identity();documents=[]
 for rule_path in sorted(RULES_DIR.glob("R*.json")):
  card=json.loads(rule_path.read_text(encoding="utf-8"))
  rule_id=card["rule_id"];status=card["status"];rule_version=card["version_history"][-1]["version"]
  body=json.dumps(card,ensure_ascii=False,indent=2,sort_keys=True)
  header="\n".join(["---",f"rule_id: {rule_id}",f"rule_version: {rule_version}",f"status: {status}",f"source_release: {identity['ontology_version']}@{identity['source_commit']}",f"package_checksum: {identity['package_checksum']}","---",""])
  documents.append({"rule_id":rule_id,"rule_version":rule_version,"status":status,"file":f"{rule_id}.md","content":header+body+"\n"})
 return identity,documents
def build_manifest(identity,documents):
 import hashlib
 return {"schema_version":"1.0","suite_id":"kb-export-v1","release_identity":identity,"documents":[{"file":d["file"],"rule_id":d["rule_id"],"rule_version":d["rule_version"],"status":d["status"],"sha256":hashlib.sha256(d["content"].encode("utf-8")).hexdigest()} for d in documents]}
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument("--write",action="store_true");p.add_argument("--out",default=str(ROOT/"kb_export"/"v1"));a=p.parse_args()
 identity,documents=export_documents();manifest=build_manifest(identity,documents)
 if not a.write:
  print(json.dumps({"status":"DRY_RUN","document_count":len(documents),"statuses":{s:sum(1 for d in documents if d["status"]==s) for s in sorted({d['status'] for d in documents})},"release_identity":identity},sort_keys=True));return 0
 out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
 for d in documents:(out/d["file"]).write_text(d["content"],encoding="utf-8")
 (out/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
 print(json.dumps({"status":"WRITTEN","out":str(out),"document_count":len(documents)},sort_keys=True));return 0
if __name__=="__main__":raise SystemExit(main())
