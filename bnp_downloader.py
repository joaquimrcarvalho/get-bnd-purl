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
        self.purl_id = self._extract_record_id()
        self.record_id = self.purl_id  # may be updated by _resolve_purl
        self.output_dir = output_dir or f"purl-{self.purl_id}-images"
        self.workers = workers
        self.uuid = None
        self.doc_prefix = None
        self.total_pages = 0

    @staticmethod
    def _fetch_html(url: str, timeout: int = 30) -> str:
        """Fetch a URL and return its HTML content as a string."""
        req = Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                          'AppleWebKit/537.36 (KHTML, like Gecko) '
                          'Chrome/120.0.0.0 Safari/537.36'
        })
        with urlopen(req, timeout=timeout) as response:
            return response.read().decode('utf-8', errors='ignore')

    def _resolve_purl(self) -> str:
        """Resolve a purl.pt URL to the internal permalinkbnd record ID.

        Tries copy numbers /1 through /6, following the redirect chain:
            purl.pt/{id}/{copy} → permalinkbnd.bnportugal.gov.pt/idurl/...
                                → /records/item/{internal_id}-slug
        Returns the internal record ID, or the original purl_id on failure.
        """
        for copy_num in range(1, 7):
            resolve_url = f"https://purl.pt/{self.purl_id}/{copy_num}"
            try:
                req = Request(resolve_url, headers={
                    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                                  'AppleWebKit/537.36 (KHTML, like Gecko) '
                                  'Chrome/120.0.0.0 Safari/537.36'
                })
                with urlopen(req, timeout=30) as response:
                    final_url = response.geturl()
                # Extract internal ID from /records/item/{id}-slug or /viewer/{id}/
                for pattern in [r'/records/item/(\d+)', r'/viewer/(\d+)', r'/idurl/\d+/(\d+)']:
                    match = re.search(pattern, final_url)
                    if match:
                        internal_id = match.group(1)
                        if internal_id != self.purl_id:
                            print(f"Resolved purl.pt/{self.purl_id} → internal ID {internal_id} (copy {copy_num})")
                        return internal_id
            except Exception:
                continue
        print(f"Warning: Could not resolve purl.pt URL, using original ID")
        return self.purl_id

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

    def _parse_iiif(self, html: str) -> bool:
        """Parse IIIF configuration from HTML. Returns True if successful."""
        iiif_match = re.search(r'IIIF=([^&"]+)', html)
        if not iiif_match:
            return False

        iiif_thumb = iiif_match.group(1)
        print(f"Found IIIF path: {iiif_thumb[:60]}...")

        # Parse UUID and document prefix
        # Pattern: /uuid/iiif/doc_prefix_page.tif/...
        parts = iiif_thumb.split('/iiif/')
        if len(parts) < 2:
            return False

        # Keep UUID as-is (includes leading /) — required for IIIF URL format
        self.uuid = parts[0]
        doc_with_page = parts[1].split('/')[0]
        doc_with_page = re.sub(r'_\d{6}\.tif$', '', doc_with_page)
        self.doc_prefix = doc_with_page
        return True

    def _detect_page_count(self, html: str) -> int:
        """Detect total pages from HTML by finding the highest 6-digit page number."""
        # Find all 6-digit zero-padded page numbers in IIIF references
        page_nums = re.findall(r'_(\d{6})\.tif', html)
        if page_nums:
            max_page = max(int(p) for p in page_nums)
            return max_page
        return 0

    def _probe_last_page(self) -> int:
        """Probe IIIF URLs to find the last valid page via binary search."""
        def page_exists(page: int) -> bool:
            url = self.get_image_url(page)
            try:
                req = Request(url, method='HEAD', headers={
                    'User-Agent': 'Mozilla/5.0 (compatible; BNP-Downloader/1.0)'
                })
                with urlopen(req, timeout=15) as resp:
                    return resp.status == 200
            except Exception:
                return False

        # Exponential search to find upper bound
        upper = 100
        while page_exists(upper) and upper < 2000:
            upper *= 2

        # Binary search between upper/2 and upper
        low, high = upper // 2, upper
        while low < high:
            mid = (low + high + 1) // 2
            if page_exists(mid):
                low = mid
            else:
                high = mid - 1
        return low

    def discover(self) -> dict:
        """Discover image server configuration from viewer page."""
        # Resolve purl.pt URLs to internal record ID
        if 'purl.pt' in self.purl_url:
            self.record_id = self._resolve_purl()

        viewer_url = f"{self.BASE_URL}/viewer/{self.record_id}/"
        print(f"Discovering from: {viewer_url}")

        html = None
        try:
            html = self._fetch_html(viewer_url)
        except HTTPError as e:
            if e.code == 403:
                # Fallback: try the /records/item/ page which may be accessible
                print(f"Viewer returned 403, trying /records/item/ fallback...")
                try:
                    records_url = f"{self.BASE_URL}/records/item/{self.record_id}"
                    html = self._fetch_html(records_url)
                except Exception:
                    pass
            if html is None:
                raise Exception(f"Failed to access viewer: {e}")

        # Parse IIIF configuration
        if not self._parse_iiif(html):
            raise Exception("Could not find IIIF URL in viewer page")

        # Detect page count: probe IIIF server for accurate count
        # (viewer HTML typically only contains a single thumbnail reference)
        html_hint = self._detect_page_count(html)
        if html_hint > 0:
            print(f"HTML hint: at least {html_hint} page(s) referenced")
        self.total_pages = self._probe_last_page()
        print(f"Probed last page: {self.total_pages}")

        print(f"Document ID: {self.purl_id}"
              + (f" (internal: {self.record_id})" if self.record_id != self.purl_id else ""))
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
        completed = 0
        total_expected = end_page - start_page + 1
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
                completed += 1

                if completed % 50 == 0 or completed == total_expected:
                    elapsed = time.time() - start_time
                    rate = completed / elapsed if elapsed > 0 else 0
                    print(f"Progress: {completed}/{total_expected} "
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
