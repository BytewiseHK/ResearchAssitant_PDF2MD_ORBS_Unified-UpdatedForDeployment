# Copyright (c) Opendatalab. All rights reserved.
import torch
import io
import pickle
import gc
import psutil
import os
import traceback
import re
import pypdfium2
from time import sleep
from pathlib import Path
import copy
import json
from loguru import logger

# Fix for PyTorch 2.6+ weights_only restriction
try:
    from doclayout_yolo.nn.tasks import YOLOv10DetectionModel
    if hasattr(torch.serialization, 'add_safe_globals'):
        torch.serialization.add_safe_globals([YOLOv10DetectionModel])
    else:
        torch.load = lambda *args, **kwargs: torch._load(*args, **kwargs, weights_only=False)
except ImportError:
    logger.warning("doclayout_yolo import failed - continuing without safety modifications")

from mineru.cli.common import convert_pdf_bytes_to_bytes_by_pypdfium2, prepare_env, read_fn
from mineru.data.data_reader_writer import FileBasedDataWriter
from mineru.utils.draw_bbox import draw_layout_bbox, draw_span_bbox
from mineru.utils.enum_class import MakeMode
from mineru.backend.vlm.vlm_analyze import doc_analyze as vlm_doc_analyze
from mineru.backend.pipeline.pipeline_analyze import doc_analyze as pipeline_doc_analyze
from mineru.backend.pipeline.pipeline_middle_json_mkcontent import union_make as pipeline_union_make
from mineru.backend.pipeline.model_json_to_middle_json import result_to_middle_json as pipeline_result_to_middle_json
from mineru.backend.vlm.vlm_middle_json_mkcontent import union_make as vlm_union_make
from mineru.utils.models_download_utils import auto_download_and_get_model_root_path

def cleanup_resources():
    """Force cleanup of resources between batches"""
    gc.collect()
    sleep(0.5)
    if 'pypdfium2' in globals():
        pypdfium2.PdfDocument.__del__ = lambda self: None

def sanitize_filename(name, max_length=40):
    """
    Strict filename sanitization for Windows compatibility
    - Limits total length (40 chars by default)
    - Removes special characters
    - Ensures no trailing spaces/dots
    """
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', name)
    name = re.sub(r'[\s.]+', '_', name)
    return name[:max_length].strip('_.')

def safe_prepare_env(output_dir, pdf_file_name, parse_method):
    """Robust directory creation with strict path controls"""
    try:
        safe_name = sanitize_filename(pdf_file_name)
        base_dir = Path(output_dir) / safe_name[:30]
        
        image_dir = base_dir / "img"
        md_dir = base_dir / "out"
        
        for d in [base_dir, image_dir, md_dir]:
            try:
                d.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                logger.warning(f"Directory creation retry for {safe_name}")
                base_dir = Path(output_dir) / f"{safe_name[:15]}"
                image_dir = base_dir / "i"
                md_dir = base_dir / "o"
                d.mkdir(parents=True, exist_ok=True)
        
        return str(image_dir), str(md_dir)
    except Exception as e:
        logger.error(f"Directory creation failed for {pdf_file_name[:20]}...: {str(e)}")
        raise

def process_single_pdf(pdf_path, output_dir):
    """Completely process one PDF file with isolated resources"""
    try:
        # 1. Read file
        with open(pdf_path, 'rb') as f:
            pdf_bytes = f.read()
        
        # 2. Prepare environment
        safe_name = sanitize_filename(pdf_path.stem)
        image_dir, md_dir = safe_prepare_env(output_dir, safe_name, "auto")
        
        # 3. Convert pages
        try:
            pdf_bytes = convert_pdf_bytes_to_bytes_by_pypdfium2(pdf_bytes, 0, None)
        except Exception as e:
            logger.warning(f"Page conversion failed, using original: {str(e)}")
        
        # 4. Process with Mineru
        infer_results, all_image_lists, all_pdf_docs, lang_list, ocr_enabled_list = pipeline_doc_analyze(
            [pdf_bytes], ["en"], parse_method="auto", formula_enable=True, table_enable=True
        )
        
        # 5. Generate outputs
        model_json = copy.deepcopy(infer_results[0])
        image_writer = FileBasedDataWriter(image_dir)
        md_writer = FileBasedDataWriter(md_dir)
        
        middle_json = pipeline_result_to_middle_json(
            infer_results[0], all_image_lists[0], all_pdf_docs[0],
            image_writer, "en", ocr_enabled_list[0], True
        )
        
        # 6. Save all outputs
        pdf_info = middle_json["pdf_info"]
        
        draw_layout_bbox(pdf_info, pdf_bytes, md_dir, f"{safe_name}_lyt.pdf")
        draw_span_bbox(pdf_info, pdf_bytes, md_dir, f"{safe_name}_spn.pdf")
        md_writer.write(f"{safe_name}_orig.pdf", pdf_bytes)
        
        md_content_str = pipeline_union_make(pdf_info, MakeMode.MM_MD, os.path.basename(image_dir))
        md_writer.write_string(f"{safe_name}.md", md_content_str)
        
        content_list = pipeline_union_make(pdf_info, MakeMode.CONTENT_LIST, os.path.basename(image_dir))
        md_writer.write_string(f"{safe_name}_cnt.json", json.dumps(content_list, ensure_ascii=False, indent=2))
        
        md_writer.write_string(f"{safe_name}_mid.json", json.dumps(middle_json, ensure_ascii=False, indent=2))
        md_writer.write_string(f"{safe_name}_mod.json", json.dumps(model_json, ensure_ascii=False, indent=2))
        
        logger.success(f"Processed: {safe_name}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to process {pdf_path.name}: {str(e)}")
        return False
    finally:
        cleanup_resources()

if __name__ == '__main__':
    # Configuration
    PDF_FOLDER = "D:\\Kareem GCAP Disserts"
    OUTPUT_DIR = "D:\\GCAPdissertsoutput"
    
    # Process each PDF independently
    success_count = 0
    total_files = 0
    
    for pdf_path in Path(PDF_FOLDER).glob('*'):
        if pdf_path.suffix.lower() not in ('.pdf', '.png', '.jpeg', '.jpg'):
            continue
            
        total_files += 1
        if process_single_pdf(pdf_path, OUTPUT_DIR):
            success_count += 1
        
        # Progress update every 10 files
        if total_files % 10 == 0:
            logger.info(f"Progress: {success_count}/{total_files} files processed")
        
        # Auto-adjust based on memory
        if psutil.virtual_memory().percent > 70:
            logger.warning("High memory usage detected - forcing garbage collection")
            cleanup_resources()
    
    logger.info(f"Finished! Successfully processed {success_count}/{total_files} files")