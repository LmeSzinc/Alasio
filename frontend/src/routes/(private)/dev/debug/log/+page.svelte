<script lang="ts">
  import LogDisplay from "$private/config/[config_name]/overview/LogDisplay.svelte";
  import type { LogDataProps } from "$private/config/[config_name]/overview/types";

  // Generate sample logs with timestamps relative to now
  const now = Math.floor(Date.now() / 1000);
  const sampleLogs: LogDataProps[] = [
    { t: now - 65, l: "DEBUG", m: "OCR pipeline initialized with 3 languages" },
    { t: now - 62, l: "INFO", m: "Device connected: 127.0.0.1:5555 [Android 14]" },
    { t: now - 58, l: "INFO", m: "Screen resolution detected: 1080x2340 @ 420dpi" },
    { t: now - 55, l: "DEBUG", m: "Template match: button_start.png @ (0.92, 420, 680)" },
    { t: now - 50, l: "INFO", m: "Task [Main] starting — stage: daily_commission" },
    { t: now - 45, l: "INFO", m: "Entering map scene: stage_1_7" },
    { t: now - 40, l: "DEBUG", m: "Pathfinding: node (12, 8) -> node (13, 8), cost=1.0" },
    { t: now - 35, l: "WARNING", m: "Low fuel detection — 3 sorties remaining, will resupply after this run" },
    { t: now - 30, l: "INFO", m: "Combat started — fleet: Mob Fleet (38k CP)" },
    { t: now - 25, l: "DEBUG", m: "Skill trigger: Unyielding Will (Lv.7) — CD remaining 4s" },
    { t: now - 22, l: "INFO", m: "Combat ended — rank: S, drops: 4 items" },
    { t: now - 20, l: "INFO", m: "Stage cleared — rewards collected: 1200 EXP, 450 coins" },
    { t: now - 18, l: "WARNING", m: "Auto-resupply triggered — ammo: 12 -> 60, fuel: 8 -> 60" },
    { t: now - 15, l: "INFO", m: "Task [Main] complete — elapsed: 5m 12s" },
    { t: now - 12, l: "ERROR", m: "Screenshot capture failed: ADB connection reset by peer",
      e: 'OSError: [Errno 104] Connection reset by peer\n  File "alasio/device/screenshot.py", line 142, in capture\n    raw = self.adb.shell(f"screencap {path}")\n  File "alasio/adb/client.py", line 89, in shell\n    raise AdbError(f"shell failed: {e}") from e' },
    { t: now - 10, l: "INFO", m: "Reconnecting ADB — attempt 1/3" },
    { t: now - 8, l: "INFO", m: "ADB reconnected — session restored" },
    { t: now - 6, l: "CRITICAL", m: "Config validation failed: scheduler.cron expression invalid",
      e: 'ValueError: Cron expression "*/5 * * *" has 5 fields, expected 6 or 7\n  File "alasio/config/validator.py", line 203, in validate_cron\n    raise ValueError(f"Cron expression {expr!r} has {parts} fields, expected 6 or 7")' },
    { t: now - 4, l: "WARNING", m: "Falling back to default scheduler config" },
    { t: now - 2, l: "CRITICAL", m: "Out of memory: Python heap 2.1 GiB / 2.0 GiB limit",
      e: 'MemoryError:\n  File "alasio/ocr/engine.py", line 67, in load_model\n    self._model = ONNXModel(path, providers=["CUDAExecutionProvider"])\n  File "alasio/ocr/onnx.py", line 44, in __init__\n    self.session = ort.InferenceSession(path, sess_options, providers=providers)' },
    { t: now - 1, l: "DEBUG", m: "GC collection: freed 1.2 MiB, 2.1 GiB still in use" },
    // Long info log to test auto line break
    { t: now, l: "INFO", m: "Task [ResourceScan] completed — scanned 1,247 asset files across 38 directories (buttons: 412, templates: 689, ui_defs: 146), matched 1,203 against known hashes, queued 44 new assets for optimization pipeline, cache hit ratio 91.7%, elapsed: 2.34s" },
    // Raw log (single long line, no timestamp prefix) to test horizontal overflow
    { t: now, l: "RAW", m: `[2026-06-05 10:15:23] [adbutils]  D  AdbDevice(serial="127.0.0.1:5555", transport_id=42, state="device")  |  serial=127.0.0.1:5555  |  properties=[ro.product.model=Pixel_7_Pro, ro.build.version.sdk=34, ro.build.version.release=14]  |  features=[shell_v2, cmd, stat_v2, list_v2, fixed_push_symlink, apex, abb, abb_exec, send_recv_v2, send_recv_v2_burst, remount_shell, swa]  |  screen=1080x2340@420  |  density=420  |  rotation=0`, r: 1 },
  ];
</script>

<div class="mx-auto flex h-full w-full flex-col gap-6 overflow-auto p-4">
  <header class="flex items-center justify-between">
    <h1 class="text-3xl font-bold tracking-tight">Log Viewer</h1>
  </header>

  <!-- Scenario 1: Limited height -->
  <section class="flex flex-col gap-2">
    <h2 class="text-muted-foreground text-sm font-medium">Limited height</h2>
    <p class="text-muted-foreground/70 text-xs">
      Log area constrained to 300px — scroll to reveal older entries.
    </p>
    <div class="max-h-[300px] overflow-hidden rounded-lg border">
      <LogDisplay class="h-full rounded-none border-none shadow-none" data={sampleLogs} />
    </div>
  </section>

  <!-- Scenario 2: Two columns with half width log -->
  <section class="flex flex-col gap-2">
    <h2 class="text-muted-foreground text-sm font-medium">Half width</h2>
    <p class="text-muted-foreground/70 text-xs">
      Log viewer spans half the page width on wide screens, with a companion panel beside it.
    </p>
    <div class="grid grid-cols-1 gap-6 lg:grid-cols-2">
      <div class="min-h-0 rounded-lg border">
        <LogDisplay class="rounded-none border-none shadow-none" data={sampleLogs} />
      </div>
      <div class="bg-card text-muted-foreground flex items-center justify-center rounded-lg border p-8 text-sm">
        Companion panel — resize the browser to see how the log viewer behaves at different viewport widths.
      </div>
    </div>
  </section>
</div>
