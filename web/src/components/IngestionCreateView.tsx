import { AlertCircle, FileArchive, LoaderCircle, UploadCloud } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { INGESTION_UPLOAD_ABORTED_ERROR, uploadIngestionJob } from "../ingestionUpload";
import type { IngestionJobDetail, IngestionTarget } from "../types";

const ACCEPTED_EXTENSIONS = [".docx", ".pptx", ".pdf", ".txt", ".zip"];
const ACCEPT = ACCEPTED_EXTENSIONS.join(",");

type IngestionCreateViewProps = {
  onCreated: (detail: IngestionJobDetail) => void;
};

export function IngestionCreateView({ onCreated }: IngestionCreateViewProps) {
  const [target, setTarget] = useState<IngestionTarget>("case");
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState("");
  const controllerRef = useRef<AbortController | null>(null);
  const requestRef = useRef(0);

  useEffect(
    () => () => {
      requestRef.current += 1;
      controllerRef.current?.abort();
    },
    []
  );

  async function chooseFile(file: File | undefined) {
    if (!file) return;
    if (!isAcceptedFile(file.name)) {
      setError("请选择 docx、pptx、pdf、txt 文档或 ZIP 压缩包");
      return;
    }

    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    const requestId = ++requestRef.current;
    setUploading(true);
    setProgress(0);
    setError("");
    try {
      const detail = await uploadIngestionJob(file, target, {
        signal: controller.signal,
        onProgress: (value) => {
          if (requestRef.current === requestId) setProgress(value);
        }
      });
      if (requestRef.current === requestId) onCreated(detail);
    } catch (caught) {
      if (requestRef.current !== requestId) return;
      const message = caught instanceof Error ? caught.message : "上传失败，请重试";
      if (message !== INGESTION_UPLOAD_ABORTED_ERROR) setError(message);
    } finally {
      if (requestRef.current === requestId) {
        setUploading(false);
        controllerRef.current = null;
      }
    }
  }

  return (
    <section className="ingestion-create ingestion-scroll-content" aria-labelledby="ingestion-create-title">
      <div className="ingestion-intro">
        <p className="eyebrow">新任务</p>
        <h2 id="ingestion-create-title">创建入库任务</h2>
        <p>选择目标知识库并上传一个文档或 ZIP，预检确认后才会开始入库。</p>
      </div>

      <fieldset className="ingestion-targets" disabled={uploading}>
        <legend>目标知识库</legend>
        <label>
          <input
            type="radio"
            aria-label="案例知识库"
            name="ingestion-target"
            value="case"
            checked={target === "case"}
            onChange={() => setTarget("case")}
          />
          <span><strong>案例知识库</strong><small>销售洞察、案例解析与切分</small></span>
        </label>
        <label>
          <input
            type="radio"
            aria-label="课程知识库"
            name="ingestion-target"
            value="course"
            checked={target === "course"}
            onChange={() => setTarget("course")}
          />
          <span><strong>课程知识库</strong><small>课程解析、摘要、标签与切分</small></span>
        </label>
      </fieldset>

      <label
        className={uploading ? "ingestion-drop-zone uploading" : "ingestion-drop-zone"}
        onDragOver={(event) => event.preventDefault()}
        onDrop={(event) => {
          event.preventDefault();
          void chooseFile(event.dataTransfer.files[0]);
        }}
      >
        <input
          type="file"
          accept={ACCEPT}
          aria-label="上传文档或 ZIP"
          onChange={(event) => {
            const file = event.currentTarget.files?.[0];
            event.currentTarget.value = "";
            void chooseFile(file);
          }}
        />
        {uploading ? (
          <LoaderCircle className="spin" size={28} aria-hidden="true" />
        ) : (
          <UploadCloud size={28} aria-hidden="true" />
        )}
        <strong>{uploading ? "正在上传并预检" : "上传文档或 ZIP"}</strong>
        <span>拖放到这里，或按 Enter / 空格选择文件</span>
        <small>支持 DOCX、PPTX、PDF、TXT、ZIP</small>
      </label>

      {uploading && (
        <div className="ingestion-upload-progress" aria-live="polite">
          <span><FileArchive size={17} aria-hidden="true" />上传进度 {progress}%</span>
          <progress max={100} value={progress} aria-label="上传进度">{progress}%</progress>
        </div>
      )}
      {error && (
        <div className="ingestion-error-box" role="alert">
          <AlertCircle size={18} aria-hidden="true" />
          <span>{error}</span>
        </div>
      )}
    </section>
  );
}

function isAcceptedFile(name: string): boolean {
  const normalized = name.toLowerCase();
  return ACCEPTED_EXTENSIONS.some((extension) => normalized.endsWith(extension));
}
