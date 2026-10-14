## Verdict

**REVISE** — 這版先別開工：換檔仍可能吃掉新存稿，續跑可能封存過期審查，驗收也容許空封存證明通過。

`bash -n` 通過；實跑 `acceptance.sh` 在 `mktemp` 就因唯讀沙箱退出 2，尚未執行任何檢查，因此無法確認計畫宣稱的 9 PASS／18 FAIL。沒有修改檔案。

## Findings

1. **high — 先 rename 再比 hash，仍不能保證新存稿不被覆蓋**

   **證據：** `plan-final.html:303` 先把 md 搬到 backup，比對後再安裝候選稿；`:425,585` 卻宣稱使用者存的 bytes 不會被覆蓋。現有測試只在 gate 後、第一次 rename 前存檔（`tests/test_legacy_audit_acceptance.py:84–91,549–562`）。

   **影響：** 編輯器若在第一次 rename 後重新建立 md，下一個 `os.replace` 仍會覆蓋這份新存稿。Rollback 的「先比 hash、再 replace」也有相同空窗。獨立 worktree 無法阻止同一棵樹的編輯器；網頁核准重跑也只拿 `jobs.lock`，沒有拿 `runner.pid`（`scripts/runner.py:831–845`）。

   **修正：** 明訂並落實 install／rollback 與其他 writer 的協調；至少與網頁核准操作共用 `jobs.lock`。換檔期間發現新檔時保留它、回報衝突，不能直接覆蓋。補上這個交錯：

   ```bash
   check "M4: an owner save between the two renames survives" py <<'PY'
   import tempfile
   from pathlib import Path
   sys.path.insert(0, "tests")
   import test_legacy_audit_acceptance as f

   patch = """
   real_replace = os.replace
   def replace_then_owner_save(src, dst):
       real_replace(src, dst)
       if (Path(src).name == "T1579-007-baihua.md"
               and Path(dst).parent.name == "backup"):
           Path(src).write_text(
               Path(dst).read_text(encoding="utf-8") + "\\nOWNER-SAVE\\n",
               encoding="utf-8")
   os.replace = replace_then_owner_save
   """
   with tempfile.TemporaryDirectory() as tmp:
       tree = Path(tmp) / "repo"
       f.build_template(tree)
       md = tree / "translations/T1579-007-baihua.md"
       expected = md.read_text(encoding="utf-8") + "\nOWNER-SAVE\n"
       done = f.drive(tree, patch, "--juans", "7", "--model", "echo-finding")
       assert done.returncode == 1, (done.stdout, done.stderr)
       assert md.read_text(encoding="utf-8") == expected
   PY
   ```

2. **high — 搬走較新的存稿後被殺，續跑沒有還原分支**

   **證據：** `plan-final.html:292` 只處理「md 不存在，backup hash 等於舊 base hash」；其他情況依 `:295` 從 md 重新複製。M4 `:483` 把缺少 md 的恢復測試留給 implementer，鎖定 oracle 沒有這條。

   **影響：** 使用者先存新稿，audit 把它 rename 到 backup，process 隨即被殺。backup hash 此時與舊 base 不同；續跑既不能走恢復分支，也不能複製不存在的 md。使用者存稿雖在 backup，正式路徑卻一直缺檔。

   **修正：** 在 md 缺失時先處理所有 backup 情況。較新的 backup 必須安全還原，再使舊 gate 失效；不能把不存在的 md 當一般重新起跑。補鎖定檢查：

   ```bash
   check "M4: resume restores a newer backup after an interrupted rename" py <<'PY'
   import tempfile
   from pathlib import Path
   sys.path.insert(0, "tests")
   import test_legacy_audit_acceptance as f

   patch = f.OWNER_EDITS_AFTER_GATE + """
   real_replace = os.replace
   def replace_then_kill(src, dst):
       real_replace(src, dst)
       if (Path(src).name == "T1579-007-baihua.md"
               and Path(dst).parent.name == "backup"):
           os._exit(9)
   os.replace = replace_then_kill
   """
   with tempfile.TemporaryDirectory() as tmp:
       tree = Path(tmp) / "repo"
       f.build_template(tree)
       killed = f.drive(tree, patch, "--juans", "7", "--model", "echo-finding")
       assert killed.returncode == 9, (killed.stdout, killed.stderr)
       job_id = next((tree / "jobs/audited").glob("*.json")).stem
       resumed = f.audit(tree, "--resume", job_id)
       assert resumed.returncode in (0, 1), (resumed.stdout, resumed.stderr)
       md = tree / "translations/T1579-007-baihua.md"
       assert md.is_file(), "the owner translation was left missing"
       assert "\nOWNER-EDIT\n" in md.read_text(encoding="utf-8")
   PY
   ```

3. **high — 安裝後續跑只看 md hash，會跳過已變更的審查輸入**

   **證據：** `plan-final.html:293` 規定 cur 等於 gate hash 就直接重建、封存、不呼叫模型；`:428` 同時承諾 policy、prompt、glossary、原文變更時重審。分支 gate 只記譯稿 hash（`a5f0779:scripts/runner.py:1709–1729`），新計畫沒有增加完整輸入版本。現有 policy 變更測試是在尚未過關的 job 上執行（`tests/test_legacy_audit_acceptance.py:498–516`）。

   **影響：** 換檔後被殺，修改 policy，再 resume，會把舊 policy 下的審查直接封存。封存步驟還會保存當下的新 policy（計畫 `:309`），與實際審查版本不一致。

   **修正：** Gate 綁定審查輸入版本；所有恢復捷徑先驗證它。封存使用實際送審的快照。補安裝後的輸入變更檢查：

   ```bash
   check "M4: a policy change invalidates an installed gate on resume" py <<'PY'
   import json, tempfile
   from pathlib import Path
   sys.path.insert(0, "tests")
   import test_legacy_audit_acceptance as f

   def reviews(tree):
       return sum(
           event.get("type") == "llm_attempt"
           and event.get("stage") in ("review_terms", "review_doctrine")
           for path in (tree / "audits").glob("*/events.jsonl")
           for event in map(json.loads, path.read_text(encoding="utf-8").splitlines()))

   with tempfile.TemporaryDirectory() as tmp:
       tree = Path(tmp) / "repo"
       f.build_template(tree)
       killed = f.drive(tree, f.KILL_AT_PAGE_BUILD,
                        "--juans", "7", "--model", "echo-finding")
       assert killed.returncode == 9, (killed.stdout, killed.stderr)
       before = reviews(tree)
       policy = tree / "prompts/audited/common_policy.txt"
       policy.write_text(policy.read_text(encoding="utf-8") + "\nNEW POLICY\n",
                         encoding="utf-8")
       job_id = next((tree / "jobs/audited").glob("*.json")).stem
       done = f.audit(tree, "--resume", job_id)
       assert done.returncode == 0, (done.stdout, done.stderr)
       assert reviews(tree) - before == 24, "the changed policy was not reviewed"
   PY
   ```

4. **high — 空封存證明就能通過驗收，是具體的最小作弊實作**

   **證據：** `tests/test_legacy_audit_acceptance.py:223–228` 只要求封存檔存在且 generic verifier 通過。`a5f0779:scripts/publisher.py:40–56` 不要求必備檔案；`files={}` 時沒有任何內容 hash 被驗證。

   **影響：** 保留其他正常流程，只讓 `create_legacy_attestation()` 呼叫 `publisher.create_attestation(..., {})`，現有 legacy 封存斷言仍會過，但譯稿、頁面、ledger 與模型證據全部沒有受到保護。分支測試檢查的是另一個 `create_volume_attestation()`，抓不到這個作弊。

   **修正：** 鎖定必備封存角色、實際路徑、manifest artifacts，以及封存後改稿必須使 resume 失敗：

   ```bash
   check "M4: legacy seals bind the required files and all artifacts" py <<'PY'
   import json, tempfile
   from pathlib import Path
   sys.path.insert(0, "tests")
   import test_legacy_audit_acceptance as f

   with tempfile.TemporaryDirectory() as tmp:
       tree = Path(tmp) / "repo"
       f.build_template(tree)
       done = f.audit(tree, "--juans", "7", "--model", "echo")
       assert done.returncode == 0, (done.stdout, done.stderr)
       report = json.loads(next((tree / "audits").glob(
           "*/T1579-007-audit-report.json")).read_text(encoding="utf-8"))
       seal = json.loads((tree / report["attestation"]).read_text(encoding="utf-8"))
       files = seal["files"]
       required = {
           "source_data": "data/T1579-007.json",
           "translation": "translations/T1579-007-baihua.md",
           "volume_html": "docs/T1579/translations/T1579-007-baihua.html",
           "coverage_ledger": f"audits/{report['job_id']}/T1579-007-coverage.json",
       }
       for role, path in required.items():
           assert files[role]["path"] == path, (role, files)
       assert "audit_event_log_snapshot" in files
       manifest = json.loads((tree / "audits" / report["job_id"] /
                              "manifest.json").read_text(encoding="utf-8"))
       sealed_paths = {item["path"] for item in files.values()}
       assert {item["path"] for item in manifest["artifacts"]} <= sealed_paths
       md = tree / required["translation"]
       md.write_text(md.read_text(encoding="utf-8") + "\nAFTER-SEAL\n",
                     encoding="utf-8")
       resumed = f.audit(tree, "--resume", report["job_id"])
       assert resumed.returncode == 1, (resumed.stdout, resumed.stderr)
   PY
   ```

5. **medium — `build_page()` 會改封存版本，超出安裝與還原範圍**

   **證據：** K3 要保留 `build_page()`（`plan-final.html:249`），K5 重建及 rollback 都呼叫它（`:304,306`）。實際函式會遍歷 `[source, *archived_versions(source)]`，補寫各版本的 md id 並重寫 HTML（`scripts/build_translation_html.py:512–526`）。State 寫入清單 `:414` 沒列封存版本；目前 repo 沒有封存版本 fixture。

   **影響：** 審查最後失敗，封存版 md／HTML 仍可能已被改寫；重新 build 無法還原它們原先的 bytes，違反 K5 的 fail 保證。

   **修正：** Legacy audit 只渲染當前卷的一頁。直接使用現有 `render()` 寫該頁，比擴大備份與 rollback 範圍簡單。同步調整測試注入介面，補這條：

   ```bash
   check "M4: a failed audit preserves archived versions byte for byte" py <<'PY'
   import tempfile
   from pathlib import Path
   sys.path.insert(0, "tests")
   import test_legacy_audit_acceptance as f

   with tempfile.TemporaryDirectory() as tmp:
       tree = Path(tmp) / "repo"
       f.build_template(tree)
       md = tree / "translations/T1579-007-baihua.md"
       archive = tree / "translations/versions/T1579-007-baihua.v1.md"
       archive.parent.mkdir(parents=True, exist_ok=True)
       archive.write_text(f.MARKER_RE.sub("", md.read_text(encoding="utf-8")),
                          encoding="utf-8")
       page = tree / "docs/T1579/translations/T1579-007-baihua.v1.html"
       page.write_bytes(b"ARCHIVED PAGE\n")
       before = f.digest(tree, "docs", "translations")
       done = f.drive(tree, f.FAIL_AT_SEAL,
                      "--juans", "7", "--model", "echo-finding")
       assert done.returncode == 1, (done.stdout, done.stderr)
       assert f.digest(tree, "docs", "translations") == before
   PY
   ```

6. **medium — 釘住測試 id，沒有釘住不可修改的 assertion**

   **證據：** K9（`plan-final.html:406–409`）禁止改 assertion；`acceptance.sh:48–62` 卻沒有鎖定那三個允許相容性修改的測試檔，`:288–293` 只看測試 id 是否回報 `ok`。

   **影響：** 在測試方法第一行加 `return`，名稱及原 assertion 都可以留著，驗收仍把它算成功。這讓「112 個原測試未被削弱」缺少 script 內的 oracle。外部 harness 是否另有完整性防護，本輪無法驗證。

   **修正：** 作者先列完所有允許的相容性改動，再鎖定修改後 AST；其餘改動由作者審核後重新上鎖。以下涵蓋 K9 已明列的三項：

   ```bash
   check "M2: branch tests contain only the reviewed adaptations" py <<'PY'
   import ast, subprocess
   from pathlib import Path

   for name in ("test_pipeline_audit.py", "test_llm_rate_limits.py",
                "test_build_translation_html.py"):
       path = Path("tests") / name
       expected = subprocess.check_output(
           ["git", "show", f"a5f0779:{path.as_posix()}"], text=True)
       if name in ("test_pipeline_audit.py", "test_llm_rate_limits.py"):
           expected = expected.replace(
               "\nimport runner\n", "\nimport audited as runner\n")
       if name == "test_pipeline_audit.py":
           expected = expected.replace(
               'ROOT / "prompts" / "translate.txt"',
               'ROOT / "prompts" / "audited" / "translate.txt"')
       if name == "test_build_translation_html.py":
           expected = expected.replace(
               'bth.render([entry], source, 70, "test")',
               'bth.render([entry], source, "T1579", 70, "test", term_style="policy")')
       assert ast.dump(ast.parse(path.read_text(encoding="utf-8"))) == \
              ast.dump(ast.parse(expected)), f"unreviewed test change: {path}"
   PY
   ```

## Questions for the plan author

None