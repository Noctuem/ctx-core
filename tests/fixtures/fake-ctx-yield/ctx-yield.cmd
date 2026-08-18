@echo off
rem Windows shim for the fake `ctx-yield` binary used by test_yield_bridge.py.
rem shutil.which("ctx-yield") only matches name+PATHEXT combinations on
rem Windows (never the bare extensionless name), so this .cmd file is what
rem actually gets found there.
python "%~dp0fake_ctx_yield.py" %*
exit /b %ERRORLEVEL%
