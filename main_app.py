import json
import os
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

# Add the dev folder to sys.path so we can import the new engine modules.
_MODULE_DIR = Path(__file__).resolve().parent / "dev"
if str(_MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(_MODULE_DIR))

from optimized_blake3 import detected_simd_tier, hash_file  # noqa: E402
from benchmark_blake3 import benchmark_files, _write_csv  # noqa: E402


class Blake3HasherApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("BLAKE3 File Hasher")
        self.geometry("760x320")
        self.minsize(680, 280)

        self.file_path_var = tk.StringVar()
        self.hash_var = tk.StringVar(value="Hash will appear here")
        simd_info = detected_simd_tier()
        self.status_var = tk.StringVar(value=f"SIMD: {simd_info} | Choose a file to hash")
        self.mode_var = tk.StringVar(value="optimized")
        self.repeats_var = tk.IntVar(value=3)

        self._busy = False

        self._build_ui()

    def _build_ui(self) -> None:
        container = ttk.Frame(self, padding=16)
        container.pack(fill="both", expand=True)

        title = ttk.Label(container, text="BLAKE3 File Hasher", font=("Segoe UI", 16, "bold"))
        title.pack(anchor="w", pady=(0, 12))

        file_row = ttk.Frame(container)
        file_row.pack(fill="x", pady=(0, 10))

        path_entry = ttk.Entry(file_row, textvariable=self.file_path_var)
        path_entry.pack(side="left", fill="x", expand=True)

        browse_btn = ttk.Button(file_row, text="Browse", command=self.browse_file)
        browse_btn.pack(side="left", padx=(8, 0))

        action_row = ttk.Frame(container)
        action_row.pack(fill="x", pady=(0, 10))

        ttk.Label(action_row, text="Mode").pack(side="left")
        mode_box = ttk.Combobox(
            action_row,
            textvariable=self.mode_var,
            values=["baseline", "optimized"],
            width=12,
            state="readonly",
        )
        mode_box.pack(side="left", padx=(8, 8))

        hash_btn = ttk.Button(action_row, text="Hash File", command=self.start_hashing)
        hash_btn.pack(side="left")

        bench_btn = ttk.Button(action_row, text="Benchmark Folder", command=self.start_benchmark)
        bench_btn.pack(side="left", padx=(8, 0))

        copy_btn = ttk.Button(action_row, text="Copy Hash", command=self.copy_hash)
        copy_btn.pack(side="left", padx=(8, 0))

        bench_opts = ttk.Frame(container)
        bench_opts.pack(fill="x", pady=(0, 10))

        ttk.Label(bench_opts, text="Repeats/file").pack(side="left")
        repeats_box = ttk.Spinbox(bench_opts, from_=1, to=20, width=5, textvariable=self.repeats_var)
        repeats_box.pack(side="left", padx=(8, 12))

        ttk.Label(bench_opts, text="(Compares BLAKE3 vs MD5/SHA-1/SHA-256)").pack(side="left")

        hash_frame = ttk.LabelFrame(container, text="BLAKE3 (hex)", padding=10)
        hash_frame.pack(fill="x", expand=False)

        hash_entry = ttk.Entry(hash_frame, textvariable=self.hash_var)
        hash_entry.pack(fill="x", expand=True)

        summary_frame = ttk.LabelFrame(container, text="Benchmark Summary", padding=10)
        summary_frame.pack(fill="both", expand=True)

        self.summary_text = tk.Text(summary_frame, height=7, wrap="word")
        self.summary_text.pack(fill="both", expand=True)
        self.summary_text.insert("1.0", "No benchmark run yet")
        self.summary_text.configure(state="disabled")

        status = ttk.Label(container, textvariable=self.status_var)
        status.pack(anchor="w", pady=(10, 0))

    def browse_file(self) -> None:
        path = filedialog.askopenfilename(title="Select a file")
        if path:
            self.file_path_var.set(path)
            self.status_var.set("Ready to hash")

    def start_hashing(self) -> None:
        if self._busy:
            messagebox.showinfo("Busy", "Please wait for the current task to finish.")
            return

        file_path = self.file_path_var.get().strip()
        if not file_path:
            messagebox.showwarning("No file selected", "Please choose a file first.")
            return

        if not os.path.isfile(file_path):
            messagebox.showerror("Invalid file", "The selected path is not a valid file.")
            return

        self._busy = True
        self.status_var.set("Hashing...")
        self.hash_var.set("Computing hash...")

        worker = threading.Thread(target=self._hash_file_worker, args=(file_path,), daemon=True)
        worker.start()

    def _hash_file_worker(self, file_path: str) -> None:
        try:
            mode = self.mode_var.get()
            # "baseline" uses latency workload (single thread, fixed buffer);
            # "optimized" uses the full adaptive/parallel profile.
            workload = "latency" if mode == "baseline" else "balanced"
            result = hash_file(file_path, workload=workload)
            if result.status != "ok":
                raise RuntimeError(result.error or "hashing failed")

            self.after(0, lambda: self._on_hash_success(
                result.digest,
                result.elapsed_ms / 1000.0,
                result.throughput_mb_s,
                result.simd_tier,
                result.threads_used,
            ))
        except Exception as exc:
            self.after(0, lambda: self._on_hash_error(str(exc)))

    def _on_hash_success(
        self, digest: str, elapsed_s: float, throughput_mb_s: float,
        simd_tier: str, threads_used: int,
    ) -> None:
        self._busy = False
        self.hash_var.set(digest)
        self.status_var.set(
            f"Hash complete | {elapsed_s:.4f}s | {throughput_mb_s:.2f} MB/s"
            f" | SIMD: {simd_tier} | Threads: {threads_used}"
        )

    def _on_hash_error(self, error_message: str) -> None:
        self._busy = False
        self.hash_var.set("Hash failed")
        self.status_var.set("Error while hashing")
        messagebox.showerror("Hashing error", error_message)

    def start_benchmark(self) -> None:
        if self._busy:
            messagebox.showinfo("Busy", "Please wait for the current task to finish.")
            return

        dataset_dir = filedialog.askdirectory(title="Select dataset folder")
        if not dataset_dir:
            return

        try:
            repeats = int(self.repeats_var.get())
        except (TypeError, ValueError):
            messagebox.showerror("Invalid repeats", "Repeats must be a whole number.")
            return

        if repeats < 1:
            messagebox.showerror("Invalid repeats", "Repeats must be at least 1.")
            return

        self._busy = True
        self.status_var.set("Benchmarking...")
        self._set_summary("Running benchmark, please wait...")

        worker = threading.Thread(
            target=self._benchmark_worker,
            args=(dataset_dir, repeats),
            daemon=True,
        )
        worker.start()

    def _benchmark_worker(self, dataset_dir: str, repeats: int) -> None:
        try:
            file_paths = [
                str(p)
                for p in Path(dataset_dir).rglob("*")
                if p.is_file()
            ]
            if not file_paths:
                raise ValueError("No files found in selected folder.")

            report = benchmark_files(file_paths, rounds=repeats)

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_json = os.path.join(dataset_dir, f"blake3_benchmark_{ts}.json")
            output_csv = os.path.join(dataset_dir, f"blake3_benchmark_{ts}.csv")
            Path(output_json).write_text(json.dumps(report, indent=2), encoding="utf-8")
            _write_csv(output_csv, report["runs"])

            self.after(0, lambda: self._on_benchmark_success(
                report["summary"], output_csv, output_json, len(file_paths), repeats
            ))
        except Exception as exc:
            self.after(0, lambda: self._on_benchmark_error(str(exc)))

    def _on_benchmark_success(
        self,
        summary: list,
        output_csv: str,
        output_json: str,
        file_count: int,
        repeats: int,
    ) -> None:
        self._busy = False
        simd_info = detected_simd_tier()
        lines = [
            f"Files processed: {file_count}",
            f"Repeats/file: {repeats}",
            f"SIMD tier: {simd_info}",
            "",
            "Median Throughput by Category & Algorithm:",
        ]

        for entry in summary:
            lines.append(
                f"  [{entry['category']}] {entry['algorithm']}: "
                f"{entry['median_throughput_mb_s']:.2f} MB/s  "
                f"(median {entry['median_elapsed_ms']:.1f} ms, {entry['runs']} runs)"
            )

        lines.append("")
        lines.append(f"CSV:  {output_csv}")
        lines.append(f"JSON: {output_json}")

        self._set_summary("\n".join(lines))
        self.status_var.set("Benchmark complete")

    def _on_benchmark_error(self, error_message: str) -> None:
        self._busy = False
        self.status_var.set("Benchmark failed")
        self._set_summary("Benchmark failed")
        messagebox.showerror("Benchmark error", error_message)

    def _set_summary(self, text: str) -> None:
        self.summary_text.configure(state="normal")
        self.summary_text.delete("1.0", "end")
        self.summary_text.insert("1.0", text)
        self.summary_text.configure(state="disabled")

    def copy_hash(self) -> None:
        digest = self.hash_var.get().strip()
        if not digest or digest in {"Hash will appear here", "Computing hash...", "Hash failed"}:
            messagebox.showinfo("Nothing to copy", "Run a hash first.")
            return

        self.clipboard_clear()
        self.clipboard_append(digest)
        self.status_var.set("Hash copied to clipboard")


if __name__ == "__main__":
    app = Blake3HasherApp()
    app.mainloop()
