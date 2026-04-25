#!/usr/bin/env python3
"""
BNP Digital Image Downloader

Downloads high resolution images from Biblioteca Nacional de Portugal digital library.

Usage:
    python bnp_downloader.py <purl_url> [--output-dir <dir>] [--pages <start-end>]
    python bnp_downloader.py 183 --output-dir ./images
    python bnp_downloader.py https://purl.pt/183 --pages 10-50
"""

import sys
import os
import re
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import HTTPError
import time


class BNPDownloader:
    """Download high resolution images from BNP digital library."""

    BASE_URL = "https://permalinkbnd.bnportugal.gov.pt"
    IMAGE_SERVER = "https://permalinkbnd.bnportugal.gov.pt/i/"

    def __init__(self, purl_url: str, output_dir: str = None, workers: int = 8):
        self.purl_url = self._normalize_purl(purl_url)
        self.record_id = self._extract_record_id()
        self.output_dir = output_dir or f"purl-{self.record_id}-images"
        self.workers = workers
        self.uuid = None
        self.doc_prefix = None
        self.total_pages = 0

    def _normalize_purl(self, url: str) -> str:
        """Normalize PURL to full URL."""
        if url.isdigit():
            return f"https://purl.pt/{url}"
        return url

    def _extract_record_id(self) -> str:
        """Extract record ID from PURL."""
        match = re.search(r'purl\.pt/(\d+)', self.purl_url)
        if match:
            return match.group(1)
        match = re.search(r'viewer/(\d+)', self.purl_url)
        if match:
            return match.group(1)
        return self.purl_url.rstrip('/').split('/')[-1]

    def discover(self) -> dict:
        """Discover image server configuration from viewer page."""
        viewer_url = f"{self.BASE_URL}/viewer/{self.record_id}/"
        print(f"Discovering from: {viewer_url}")

        try:
            req = Request(viewer_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urlopen(req, timeout=30) as response:
                html = response.read().decode('utf-8', errors='ignore')
        except HTTPError as e:
            raise Exception(f"Failed to access viewer: {e}")

        # Find IIIF URL in HTML
        iiif_match = re.search(r'IIIF=([^&"]+)', html)
        if not iiif_match:
            raise Exception("Could not find IIIF URL in viewer page")

        iiif_thumb = iiif_match.group(1)
        print(f"Found IIIF path: {iiif_thumb[:50]}...")

        # Parse UUID and document prefix
        # Pattern: /uuid/iiif/doc_prefix_page.tif/...
        parts = iiif_thumb.split('/iiif/')
        if len(parts) < 2:
            raise Exception("Invalid IIIF URL format")

        self.uuid = parts[0]
        doc_with_page = parts[1].split('/')[0]
        doc_with_page = re.sub(r'_\d{6}\.tif$', '', doc_with_page)
        self.doc_prefix = doc_with_page

        # Find total pages from the HTML
        pages_match = re.search(r'/(\d+)\s*$', html, re.MULTILINE)
        if pages_match:
            self.total_pages = int(pages_match.group(1))
        else:
            self.total_pages = 384  # Default fallback

        print(f"Document ID: {self.record_id}")
        print(f"UUID: {self.uuid}")
        print(f"Prefix: {self.doc_prefix}")
        print(f"Total pages: {self.total_pages}")

        return {
            'uuid': self.uuid,
            'prefix': self.doc_prefix,
            'total_pages': self.total_pages
        }

    def get_image_url(self, page_num: int) -> str:
        """Generate URL for a specific page."""
        padded = f"{page_num:06d}"
        iiif_path = f"{self.uuid}/iiif/{self.doc_prefix}_{padded}.tif/full/max/0/default.jpg"
        return f"{self.IMAGE_SERVER}?IIIF={iiif_path}"

    def download_page(self, page_num: int, output_dir: Path) -> tuple:
        """Download a single page."""
        url = self.get_image_url(page_num)
        output_file = output_dir / f"hires_{page_num:06d}.jpg"

        if output_file.exists():
            return page_num, 'skipped', 0

        try:
            req = Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (compatible; BNP-Downloader/1.0)'
            })
            with urlopen(req, timeout=60) as response:
                data = response.read()
                with open(output_file, 'wb') as f:
                    f.write(data)
                return page_num, 'success', len(data)
        except HTTPError:
            return page_num, 'error', 0
        except Exception:
            return page_num, 'error', 0

    def download_all(self, start_page: int = 1, end_page: int = None) -> dict:
        """Download all pages in parallel."""
        if end_page is None:
            end_page = self.total_pages

        output_dir = Path(self.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        print(f"\nDownloading pages {start_page} to {end_page} to {output_dir}")
        print(f"Using {self.workers} parallel workers\n")

        stats = {'success': 0, 'error': 0, 'skipped': 0, 'total_bytes': 0}
        start_time = time.time()

        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            futures = {
                executor.submit(self.download_page, i, output_dir): i
                for i in range(start_page, end_page + 1)
            }

            for future in as_completed(futures):
                page, status, size = future.result()
                stats[status] += 1
                stats['total_bytes'] += size

                if stats['success'] % 50 == 0 or stats['success'] + stats['error'] == end_page - start_page + 1:
                    elapsed = time.time() - start_time
                    rate = stats['success'] / elapsed if elapsed > 0 else 0
                    print(f"Progress: {stats['success'] + stats['error']}/{end_page - start_page + 1} "
                          f"(+{stats['error']} errors) - {rate:.1f} pages/sec")

        elapsed = time.time() - start_time
        print(f"\nDownload complete!")
        print(f"  Success: {stats['success']}")
        print(f"  Errors: {stats['error']}")
        print(f"  Skipped: {stats['skipped']}")
        print(f"  Total: {stats['total_bytes'] / 1024 / 1024:.1f} MB")
        print(f"  Time: {elapsed:.1f} seconds")
        print(f"  Location: {output_dir.absolute()}")

        return stats


def main():
    parser = argparse.ArgumentParser(
        description='Download high resolution images from BNP digital library'
    )
    parser.add_argument('purl', help='PURL URL or number (e.g., 183 or https://purl.pt/183)')
    parser.add_argument('--output', '-o', help='Output directory')
    parser.add_argument('--pages', help='Page range (e.g., 10-50)')
    parser.add_argument('--workers', '-w', type=int, default=8, help='Parallel workers')

    args = parser.parse_args()

    try:
        downloader = BNPDownloader(args.purl, args.output, args.workers)
        config = downloader.discover()

        start_page, end_page = 1, config['total_pages']
        if args.pages:
            parts = args.pages.split('-')
            start_page = int(parts[0])
            end_page = int(parts[1]) if len(parts) > 1 else start_page

        downloader.download_all(start_page, end_page)

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
