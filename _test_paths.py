"""Tests for app/data paths and logging setup (issue #6): config.json and
powermate.log must live next to the app, not in the current working directory."""
import os, sys, time, threading, traceback, tempfile, shutil, subprocess, logging

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

fails = []
def check(name, fn):
    print(f"... {name}", flush=True)
    try:
        fn(); print(f"[PASS] {name}", flush=True)
    except Exception as e:
        fails.append(name); print(f"[FAIL] {name}: {e}"); traceback.print_exc()

def watchdog():
    time.sleep(60); print("[WATCHDOG] timeout"); os._exit(3)
threading.Thread(target=watchdog, daemon=True).start()


def _same(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


class _InTempCwd:
    """chdir into a fresh temp folder for the duration of the block."""
    def __enter__(self):
        self.old = os.getcwd()
        self.dir = tempfile.mkdtemp(prefix="pm_cwd_")
        os.chdir(self.dir)
        return self.dir
    def __exit__(self, *exc):
        os.chdir(self.old)
        shutil.rmtree(self.dir, ignore_errors=True)


class _DataDirAt:
    """Pin paths' cached data folder to a temp folder (no probing of the repo)."""
    def __init__(self, d):
        self.d = d
    def __enter__(self):
        import paths
        self.saved = (paths._data_dir, paths._fallback_used)
        paths._data_dir, paths._fallback_used = self.d, False
        return self.d
    def __exit__(self, *exc):
        import paths
        paths._data_dir, paths._fallback_used = self.saved


class _RootLogging:
    """Save/restore the root logger so setup_logging() calls don't leak."""
    def __enter__(self):
        root = logging.getLogger()
        self.saved = (root.level, list(root.handlers))
        return root
    def __exit__(self, *exc):
        root = logging.getLogger()
        for h in list(root.handlers):
            if h not in self.saved[1]:
                root.removeHandler(h)
                h.close()
        root.handlers[:] = self.saved[1]
        root.setLevel(self.saved[0])


def test_1_app_dir_ignores_cwd():
    # (1) Changing the CWD must not move the app folder, bundled resources, or
    # the default config file.
    with _InTempCwd() as cwd:
        import paths
        assert _same(paths.app_dir(), REPO), (paths.app_dir(), REPO)

        import tray
        icon = tray.resource_path("assets/icon.ico")
        assert _same(os.path.dirname(os.path.dirname(icon)), REPO), icon
        assert os.path.exists(icon), icon

        data = tempfile.mkdtemp(prefix="pm_data_")
        try:
            with _DataDirAt(data):
                from config import Config
                cfg = Config()
                assert _same(cfg.config_file, os.path.join(data, "config.json")), cfg.config_file
                assert os.path.exists(os.path.join(data, "config.json"))
            assert not os.path.exists(os.path.join(cwd, "config.json")), os.listdir(cwd)
        finally:
            shutil.rmtree(data, ignore_errors=True)


def test_2_data_dir_falls_back_to_localappdata():
    # (2) App folder not writable -> %LOCALAPPDATA%\PowerMate (created).
    import paths
    tmp = tempfile.mkdtemp(prefix="pm_lad_")
    not_a_dir = os.path.join(tmp, "app_is_a_file")
    open(not_a_dir, "w").close()               # mkstemp inside it fails -> "not writable"
    lad = os.path.join(tmp, "LocalAppData")
    saved = (paths._data_dir, paths._fallback_used, paths.app_dir, os.environ.get("LOCALAPPDATA"))
    try:
        os.environ["LOCALAPPDATA"] = lad
        paths.app_dir = lambda: not_a_dir
        paths._data_dir, paths._fallback_used = None, False
        d = paths.data_dir()
        assert _same(d, os.path.join(lad, "PowerMate")), d
        assert os.path.isdir(d)
        assert paths.fallback_used()
        assert _same(paths.data_path("config.json"), os.path.join(lad, "PowerMate", "config.json"))

        # Writable app folder -> the app folder itself, no fallback, probe cleaned up.
        app = os.path.join(tmp, "app")
        os.mkdir(app)
        paths.app_dir = lambda: app
        paths._data_dir, paths._fallback_used = None, False
        assert _same(paths.data_dir(), app), paths.data_dir()
        assert not paths.fallback_used()
        assert os.listdir(app) == [], os.listdir(app)
    finally:
        paths._data_dir, paths._fallback_used, paths.app_dir = saved[:3]
        if saved[3] is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = saved[3]
        shutil.rmtree(tmp, ignore_errors=True)


def test_3_logging_survives_file_handler_failure():
    # (3) If the log file can't be opened, setup_logging still finishes and
    # leaves a console handler.
    import paths, main
    tmp = tempfile.mkdtemp(prefix="pm_log_")
    try:
        bad = os.path.join(tmp, "missing_dir")   # FileHandler can't create a file here
        with _RootLogging() as root, _DataDirAt(bad):
            main.setup_logging(["main.py"])
            kinds = [type(h) for h in root.handlers]
            assert logging.StreamHandler in kinds, kinds
            assert not any(issubclass(k, logging.FileHandler) for k in kinds), kinds
            logging.getLogger("t").info("still logging")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_4_debug_arg_sets_debug_level():
    # (4) `python main.py debug` -> DEBUG, otherwise INFO; log file in the data folder.
    import main
    tmp = tempfile.mkdtemp(prefix="pm_dbg_")
    try:
        with _InTempCwd() as cwd, _DataDirAt(tmp):
            with _RootLogging() as root:
                main.setup_logging(["main.py", "debug"])
                assert root.level == logging.DEBUG, root.level
                files = [h for h in root.handlers if isinstance(h, logging.FileHandler)]
                assert len(files) == 1 and _same(files[0].baseFilename,
                                                 os.path.join(tmp, "powermate.log")), files
            with _RootLogging() as root:
                main.setup_logging(["main.py"])
                assert root.level == logging.INFO, root.level
            assert not os.path.exists(os.path.join(cwd, "powermate.log"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_5_import_main_creates_nothing_in_cwd():
    # DoD: importing main (without running the app) writes nothing to the CWD.
    with _InTempCwd() as cwd:
        r = subprocess.run([sys.executable, "-c",
                            "import sys; sys.path.insert(0, sys.argv[1]); import main",
                            REPO], cwd=cwd, capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr[-2000:]
        left = [n for n in os.listdir(cwd) if n in ("powermate.log", "config.json")]
        assert not left, left


for name, fn in list(globals().items()):
    if name.startswith("test_") and callable(fn):
        check(name, fn)

print("\n" + ("ALL PATH TESTS PASSED" if not fails else f"FAILURES: {fails}"))
sys.exit(1 if fails else 0)
