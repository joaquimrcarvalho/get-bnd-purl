# get_bnd_purl

Download high resolution images from the Biblioteca Nacional de Portugal digital library (purl.pt).

A Python CLI tool and AI agent skill for downloading full-resolution images from the BNP digital archive. Supports any document hosted on purl.pt or permalinkbnd.bnportugal.gov.pt.

## Features

- Automatic discovery of image server configuration from PURL URLs
- High resolution image download (~2450x3700 pixels, suitable for OCR)
- Parallel downloads for fast batch acquisition
- Works as a standalone Python script or as a MiniMax Agent skill
- Supports page range selection

## Quick Start

### Installation

```bash
# Clone the repository
git clone https://github.com/joaquimrcarvalho/get-bnd-purl.git
cd get_bnd_purl

# Make the script executable
chmod +x bnp_downloader.py
```

Some agents are able to install the skill directly from the repository at clone https://github.com/joaquimrcarvalho/get-bnd-purl.git . Tested: MiniMax, Qoder Work

### Usage

```bash
# Download all pages from a document
./bnp_downloader.py 183

# Download to a specific folder
./bnp_downloader.py 183 --output-dir ./my-images

# Download specific page range
./bnp_downloader.py https://purl.pt/183 --pages 10-50

# Use more parallel workers for faster download
./bnp_downloader.py 183 --workers 16
```

## Requirements

- Python 3.7+
- Standard library only (uses urllib, concurrent.futures)

## How It Works

The tool automatically discovers the IIIF image server configuration by:

1. Accessing the BNP viewer page for the document
2. Extracting the IIIF URL and document identifier from the HTML
3. Generating high-resolution image URLs using the discovered pattern
4. Downloading all pages in parallel

## Examples

### Download the BNP Manuscriptos Inventário

```bash
./bnp_downloader.py https://purl.pt/183 --output-dir inventario-manuscritos
```

### Download pages 100-200 for OCR processing

```bash
./bnp_downloader.py https://purl.pt/183 --pages 100-200 --output-dir pages-100-200
```

## License

MIT License - see [LICENSE](LICENSE) for details.

## Author

Joaquim Ramos de Carvalho ([https://github.com/joaquimrcarvalho](https://github.com/joaquimrcarvalho)) with MiniMax and Qoder (Qwen).
