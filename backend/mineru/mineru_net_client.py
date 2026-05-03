"""
MinerU.net SaaS client (PDF → Markdown). Adapted from projects/mcp/src/mineru/api.py
without the `mineru` package name collision with PyPI mineru.

Polling defaults (see Settings in main.py): max_retries × retry_interval caps total wait
(e.g. 100 × 5s). Large PDFs may need higher limits or a future async job + status API
so the browser does not hold one HTTP request open for the full MinerU.net runtime.
"""

from __future__ import annotations

import asyncio
import logging
import os
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

import httpx

logger = logging.getLogger(__name__)


class MinerUNetError(Exception):
    """Raised when MinerU.net returns an error or unexpected payload."""


class MinerUNetClient:
    """HTTP client for https://mineru.net (Bearer API key)."""

    def __init__(
        self,
        api_base: str,
        api_key: str,
        *,
        timeout_seconds: float = 120.0,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key.strip()
        self._timeout = httpx.Timeout(timeout_seconds, connect=30.0)
        if not self.api_key:
            raise ValueError("MINERU_API_KEY is required for cloud PDF extraction.")

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }

    async def _request_json(
        self,
        client: httpx.AsyncClient,
        method: str,
        endpoint: str,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        url = f"{self.api_base}{endpoint}"
        headers = kwargs.pop("headers", {})
        merged = {**self._headers(), **headers}
        resp = await client.request(method, url, headers=merged, **kwargs)
        resp.raise_for_status()
        return resp.json()

    async def submit_file_task(
        self,
        client: httpx.AsyncClient,
        files: Union[str, List[Union[str, Dict[str, Any]]], Dict[str, Any]],
        enable_ocr: bool = True,
        language: str = "en",
        page_ranges: Optional[str] = None,
    ) -> Dict[str, Any]:
        files_config: List[Dict[str, Any]] = []

        if isinstance(files, str):
            file_path = Path(files)
            if not file_path.exists():
                raise FileNotFoundError(f"File not found: {file_path}")
            files_config.append(
                {
                    "path": file_path,
                    "name": file_path.name,
                    "is_ocr": enable_ocr,
                    "page_ranges": page_ranges,
                }
            )
        elif isinstance(files, list):
            for file_item in files:
                if isinstance(file_item, str):
                    file_path = Path(file_item)
                    if not file_path.exists():
                        raise FileNotFoundError(f"File not found: {file_path}")
                    files_config.append(
                        {
                            "path": file_path,
                            "name": file_path.name,
                            "is_ocr": enable_ocr,
                            "page_ranges": page_ranges,
                        }
                    )
                elif isinstance(file_item, dict):
                    if "path" not in file_item and "name" not in file_item:
                        raise ValueError(f"File config must include 'path' or 'name': {file_item}")
                    if "path" in file_item:
                        file_path = Path(file_item["path"])
                        if not file_path.exists():
                            raise FileNotFoundError(f"File not found: {file_path}")
                        file_name = file_path.name
                    else:
                        file_name = file_item["name"]
                        file_path = None
                    file_is_ocr = file_item.get("is_ocr", enable_ocr)
                    file_page_ranges = file_item.get("page_ranges", page_ranges)
                    fc: Dict[str, Any] = {
                        "path": file_path,
                        "name": file_name,
                        "is_ocr": file_is_ocr,
                    }
                    if file_page_ranges is not None:
                        fc["page_ranges"] = file_page_ranges
                    files_config.append(fc)
                else:
                    raise TypeError(f"Unsupported file item type: {type(file_item)}")
        elif isinstance(files, dict):
            if "path" not in files and "name" not in files:
                raise ValueError(f"File config must include 'path' or 'name': {files}")
            if "path" in files:
                file_path = Path(files["path"])
                if not file_path.exists():
                    raise FileNotFoundError(f"File not found: {file_path}")
                file_name = file_path.name
            else:
                file_name = files["name"]
                file_path = None
            file_is_ocr = files.get("is_ocr", enable_ocr)
            file_page_ranges = files.get("page_ranges", page_ranges)
            fc = {"path": file_path, "name": file_name, "is_ocr": file_is_ocr}
            if file_page_ranges is not None:
                fc["page_ranges"] = file_page_ranges
            files_config.append(fc)
        else:
            raise TypeError(f"files must be str, list, or dict, not {type(files)}")

        files_payload = []
        for file_config in files_config:
            fp: Dict[str, Any] = {
                "name": file_config["name"],
                "is_ocr": file_config["is_ocr"],
            }
            if file_config.get("page_ranges") is not None:
                fp["page_ranges"] = file_config["page_ranges"]
            files_payload.append(fp)

        payload = {"language": language, "files": files_payload}
        response = await self._request_json(
            client, "POST", "/api/v4/file-urls/batch", json=payload
        )

        if (
            "data" not in response
            or "batch_id" not in response["data"]
            or "file_urls" not in response["data"]
        ):
            raise MinerUNetError(f"Failed to get upload URLs: {response}")

        batch_id = response["data"]["batch_id"]
        file_urls = response["data"]["file_urls"]

        if len(file_urls) != len(files_config):
            raise MinerUNetError(
                f"Upload URL count ({len(file_urls)}) != file count ({len(files_config)})"
            )

        uploaded_files: List[str] = []
        upload_timeout = httpx.Timeout(300.0, connect=60.0)
        async with httpx.AsyncClient(timeout=upload_timeout) as upload_client:
            for file_config, upload_url in zip(files_config, file_urls):
                file_path = file_config["path"]
                if file_path is None:
                    raise ValueError(f"No path for file {file_config['name']}")
                data = file_path.read_bytes()
                put_resp = await upload_client.put(
                    upload_url,
                    content=data,
                    headers={},
                )
                if put_resp.status_code != 200:
                    raise MinerUNetError(
                        f"Upload failed {put_resp.status_code}: {put_resp.text[:500]}"
                    )
                uploaded_files.append(file_path.name)
                logger.info("Uploaded %s to MinerU.net staging", file_path.name)

        result = {"data": {"batch_id": batch_id, "uploaded_files": uploaded_files}}
        if len(uploaded_files) == 1:
            result["data"]["file_name"] = uploaded_files[0]
        return result

    async def get_batch_task_status(
        self, client: httpx.AsyncClient, batch_id: str
    ) -> Dict[str, Any]:
        return await self._request_json(
            client, "GET", f"/api/v4/extract-results/batch/{batch_id}"
        )

    async def process_file_to_markdown(
        self,
        client: httpx.AsyncClient,
        task_fn: Callable[..., Any],
        task_arg: Union[str, List[Dict[str, Any]], Dict[str, Any]],
        *,
        enable_ocr: bool = True,
        language: str = "en",
        output_dir: Optional[Union[str, Path]] = None,
        max_retries: int = 100,
        retry_interval: int = 5,
    ) -> Dict[str, Any]:
        task_info = await task_fn(task_arg, enable_ocr, language)
        batch_id = task_info["data"]["batch_id"]
        uploaded_files = task_info["data"].get("uploaded_files", [])
        if not uploaded_files and "file_name" in task_info["data"]:
            uploaded_files = [task_info["data"]["file_name"]]
        if not uploaded_files:
            raise MinerUNetError("Could not determine uploaded file names from task response")

        output_path = Path(output_dir or "./mineru_net_output")
        output_path.mkdir(parents=True, exist_ok=True)

        files_status: Dict[str, str] = {}
        files_download_urls: Dict[str, str] = {}
        failed_files: Dict[str, str] = {}

        for i in range(max_retries):
            status_info = await self.get_batch_task_status(client, batch_id)

            if "data" not in status_info or "extract_result" not in status_info["data"]:
                logger.error("Bad batch status payload: %s", status_info)
                await asyncio.sleep(retry_interval)
                continue

            has_progress = False
            for result in status_info["data"]["extract_result"]:
                file_name = result.get("file_name")
                if not file_name:
                    continue
                if file_name not in files_status:
                    files_status[file_name] = "pending"
                state = result.get("state")
                files_status[file_name] = state or "unknown"

                if state == "done":
                    full_zip_url = result.get("full_zip_url")
                    if full_zip_url:
                        files_download_urls[file_name] = full_zip_url
                        logger.info("MinerU.net finished: %s", file_name)
                    else:
                        pass
                elif state in ("failed", "error"):
                    failed_files[file_name] = result.get("err_msg", "unknown error")
                    logger.warning("MinerU.net failed for %s: %s", file_name, failed_files[file_name])
                else:
                    if state == "running" and "extract_progress" in result:
                        has_progress = True
                        prog = result["extract_progress"]
                        extracted = prog.get("extracted_pages", 0)
                        total = prog.get("total_pages", 0)
                        if total > 0:
                            logger.info(
                                "MinerU.net progress %s: %s/%s pages",
                                file_name,
                                extracted,
                                total,
                            )

            if uploaded_files and all(
                (n in files_download_urls or n in failed_files) for n in uploaded_files
            ):
                break

            if not has_progress:
                logger.info("Waiting for MinerU.net batch %s (%s/%s)", batch_id, i + 1, max_retries)
            await asyncio.sleep(retry_interval)
        else:
            if not files_download_urls and not failed_files:
                raise TimeoutError(f"MinerU.net batch {batch_id} did not complete in time")

        extract_dir = output_path / batch_id
        extract_dir.mkdir(parents=True, exist_ok=True)
        results: List[Dict[str, Any]] = []

        download_client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=60.0))
        try:
            for file_name, download_url in files_download_urls.items():
                try:
                    zip_file_name = download_url.split("/")[-1]
                    zip_dir_name = os.path.splitext(zip_file_name)[0]
                    file_extract_dir = extract_dir / zip_dir_name
                    file_extract_dir.mkdir(parents=True, exist_ok=True)
                    zip_path = output_path / f"{batch_id}_{zip_file_name}"

                    resp = await download_client.get(
                        download_url,
                        headers={"Authorization": f"Bearer {self.api_key}"},
                    )
                    resp.raise_for_status()
                    zip_path.write_bytes(resp.content)

                    with zipfile.ZipFile(zip_path, "r") as zip_ref:
                        zip_ref.extractall(file_extract_dir)
                    zip_path.unlink(missing_ok=True)

                    markdown_content = ""
                    md_files = list(file_extract_dir.glob("*.md"))
                    if md_files:
                        markdown_content = md_files[0].read_text(encoding="utf-8")

                    results.append(
                        {
                            "filename": file_name,
                            "status": "success",
                            "content": markdown_content,
                            "extract_path": str(file_extract_dir),
                        }
                    )
                except Exception as e:
                    logger.exception("Download/extract failed for %s", file_name)
                    results.append(
                        {
                            "filename": file_name,
                            "status": "error",
                            "error_message": str(e),
                        }
                    )
        finally:
            await download_client.aclose()

        for file_name, error_msg in failed_files.items():
            results.append(
                {
                    "filename": file_name,
                    "status": "error",
                    "error_message": f"processing failed: {error_msg}",
                }
            )

        return {
            "results": results,
            "extract_dir": str(extract_dir),
            "success_count": len(files_download_urls),
            "fail_count": len(failed_files),
            "total_count": len(files_download_urls) + len(failed_files),
        }


async def convert_pdf_path_to_markdown(
    pdf_path: str,
    *,
    api_base: str,
    api_key: str,
    language: str = "en",
    output_dir: str,
    max_retries: int = 100,
    retry_interval: int = 5,
    enable_ocr: bool = True,
) -> str:
    """
    Upload a local PDF to MinerU.net, poll until done, return markdown text.
    """
    client_net = MinerUNetClient(api_base, api_key)

    async with httpx.AsyncClient(timeout=client_net._timeout) as client:

        async def submit_wrapper(
            task_arg: Union[str, List[Dict[str, Any]], Dict[str, Any]],
            ocr: bool,
            lang: str,
        ) -> Dict[str, Any]:
            return await client_net.submit_file_task(
                client, task_arg, enable_ocr=ocr, language=lang
            )

        out = await client_net.process_file_to_markdown(
            client,
            submit_wrapper,
            pdf_path,
            enable_ocr=enable_ocr,
            language=language,
            output_dir=output_dir,
            max_retries=max_retries,
            retry_interval=retry_interval,
        )

    for row in out.get("results", []):
        if row.get("status") == "success" and row.get("content"):
            return str(row["content"])

    errs = [r.get("error_message", r) for r in out.get("results", [])]
    raise MinerUNetError(f"No markdown produced. Results: {errs}")
