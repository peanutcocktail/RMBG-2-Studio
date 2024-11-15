import os
import gradio as gr
from gradio_imageslider import ImageSlider
from loadimg import load_img
from transformers import AutoModelForImageSegmentation
import torch
from torchvision import transforms
from datetime import datetime
import devicetorch
from PIL import Image
import numpy as np
import subprocess
import requests
from io import BytesIO
import re
import glob
from pathlib import Path
from tqdm import tqdm
from PIL import ImageEnhance, ImageOps
import colorsys
import cv2


import warnings
warnings.filterwarnings('ignore', category=FutureWarning, module='timm')

device = devicetorch.get(torch)
torch.set_float32_matmul_precision(["high", "highest"][0])

birefnet = AutoModelForImageSegmentation.from_pretrained(
    "briaai/RMBG-2.0", trust_remote_code=True
)
birefnet = devicetorch.to(torch, birefnet)

transform_image = transforms.Compose([
    transforms.Resize((1024, 1024)),
    transforms.ToTensor(),
    transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
])

output_folder = '../output_images'
if not os.path.exists(output_folder):
    os.makedirs(output_folder)

def generate_filename(prefix="no_bg"):
    timestamp = datetime.now().strftime("%y%m%d_%H%M%S")
    return f"{prefix}_{timestamp}.png"

def open_output_folder():
    folder_path = os.path.abspath(output_folder)
    try:
        if os.name == 'nt':  # Windows
            os.startfile(folder_path)
        elif os.name == 'posix':  # macOS and Linux
            subprocess.run(['xdg-open' if os.name == 'posix' else 'open', folder_path])
        return "✅ Opened outputs folder. Tends to be shy and hides behind active windows."
    except Exception as e:
        return f"❌ Error opening folder: {str(e)}"

def is_valid_image_url(url):
    """Validate if the URL points to an image file."""
    try:
        # Check if URL pattern is valid
        if not re.match(r'https?://.+', url):
            return False
        
        # Check if URL responds and is an image
        response = requests.head(url, timeout=5)
        content_type = response.headers.get('content-type', '').lower()
        return (response.status_code == 200 and 
                any(img_type in content_type for img_type in ['image/jpeg', 'image/png', 'image/gif', 'image/webp']))
    except requests.ConnectionError:
        raise ConnectionError("Unable to connect. Please check your internet connection")
    except requests.Timeout:
        raise TimeoutError("Request timed out. The server took too long to respond")
    except:
        raise ValueError("Failed to validate URL")

def download_image_from_url(url):
    """Download image from URL and return as PIL Image."""
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return Image.open(BytesIO(response.content))
    except requests.ConnectionError:
        raise ConnectionError("Unable to connect. Please check your internet connection")
    except requests.Timeout:
        raise TimeoutError("Request timed out. The server took too long to respond")
    except requests.HTTPError as e:
        if e.response.status_code == 404:
            raise ValueError("Image not found (404 error)")
        elif e.response.status_code == 403:
            raise ValueError("Access to image denied (403 error)")
        else:
            raise ValueError(f"HTTP error occurred (Status code: {e.response.status_code})")
    except Exception as e:
        raise ValueError(f"Failed to download image: {str(e)}")
        
def process_input(input_data):
    """Process either uploaded image or URL input."""
    try:
        if isinstance(input_data, str) and input_data.strip():
            # Handle URL input
            url = input_data.strip()
            try:
                if not is_valid_image_url(url):
                    return None, "❌ Invalid image URL. Please ensure the URL directly links to an image (jpg, png, gif, or webp)"
                image = download_image_from_url(url)
                return image, "✅ Successfully downloaded and processed image from URL"
            except ConnectionError:
                return None, "❌ No internet connection. Please check your network and try again"
            except TimeoutError:
                return None, "❌ Connection timed out. The server took too long to respond"
            except ValueError as e:
                return None, f"❌ {str(e)}"
        else:
            # Handle direct image upload
            image = load_img(input_data, output_type="pil")
            return image, None  # None means don't update status for regular uploads
    except Exception as e:
        return None, f"❌ Error: {str(e)}"
        
        
def batch_process_images(files, progress=gr.Progress()):
    """Process multiple images and return statistics."""
    results = {
        'successful': 0,
        'failed': 0,
        'processed_files': []
    }
    
    try:
        total_files = len(files)
        for i, file in enumerate(files):
            try:
                # Update progress bar
                progress(i/total_files, f"Processing {i+1}/{total_files}")
                
                # Load and process image
                img = load_img(file.name, output_type="pil")
                img = img.convert("RGB")
                processed = process(img)
                
                # Save with original filename plus suffix
                original_name = Path(file.name).stem
                new_filename = f"{original_name}_nobg.png"
                output_path = os.path.join(output_folder, new_filename)
                processed.save(output_path)
                
                results['successful'] += 1
                results['processed_files'].append(new_filename)
                
            except Exception as e:
                results['failed'] += 1
                print(f"Failed to process {file.name}: {str(e)}")
                
        return (f"✅ Processing complete!\n"
                f"Successfully processed: {results['successful']} images\n"
                f"Failed: {results['failed']} images\n"
                f"Output saved to: {output_folder}"), update_gallery()
                
    except Exception as e:
        return f"❌ Batch processing error: {str(e)}", update_gallery()
        
        
def fn(image_input):
    if image_input is None:
        return None, update_gallery(), "⚠️ No image provided"
    
    image, status_msg = process_input(image_input)
    if image is None:
        return None, update_gallery(), status_msg
    
    origin = image.copy()
    processed_image = process(image)    
    unique_filename = generate_filename()
    image_path = os.path.join(output_folder, unique_filename)
    processed_image.save(image_path)
    gallery_paths = update_gallery()
    
    # Return status message only for URL processing
    return (processed_image, origin), gallery_paths, status_msg
    
    
def process(image):
    image_size = image.size
    input_images = transform_image(image).unsqueeze(0)
    input_images = devicetorch.to(torch, input_images) 
    with torch.no_grad():
        preds = birefnet(input_images)[-1].sigmoid().cpu()
    pred = preds[0].squeeze()
    pred_pil = transforms.ToPILImage()(pred)
    mask = pred_pil.resize(image_size)
    image.putalpha(mask)
    devicetorch.empty_cache(torch)
    return image


# Gallery management
gallery_paths = []

def update_gallery():
    global gallery_paths
    gallery_paths = [
        os.path.join(output_folder, f) 
        for f in os.listdir(output_folder) 
        if f.endswith(".png")  # Include all PNG files
    ]
    # Sort by file modification time, newest first
    gallery_paths.sort(key=lambda x: os.path.getmtime(x), reverse=True)
    return gallery_paths


        
def combine_images(fg_path, bg_path, scale, x_offset=0, y_offset=0, flip_h=False, flip_v=False, 
                  rotation=0, brightness=1.0, contrast=1.0, saturation=1.0, 
                  temperature=0, tint_color=None, tint_strength=0):
    if not (fg_path and bg_path):
        return None

    # Process foreground image
    if isinstance(fg_path, str) and fg_path.startswith(output_folder):
        fg = Image.open(fg_path)
    else:
        fg = load_img(fg_path, output_type="pil")
        fg = process(fg)
    
    # Apply color adjustments to foreground
    fg = apply_color_adjustments(
        fg, brightness, contrast, saturation,
        temperature, tint_color, tint_strength
    )
    
    bg = Image.open(bg_path) if isinstance(bg_path, str) else bg_path
    
    if fg.mode != 'RGBA':
        fg = fg.convert('RGBA')
    
    bg = bg.convert('RGBA')
    
    # Apply transformations
    if flip_h:
        fg = fg.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if flip_v:
        fg = fg.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    if rotation:
        fg = fg.rotate(rotation, expand=True, resample=Image.Resampling.BICUBIC)
    
    new_width = int(fg.size[0] * (scale / 100))
    new_height = int(fg.size[1] * (scale / 100))
    fg = fg.resize((new_width, new_height), Image.Resampling.LANCZOS)
    
    center_x = (bg.size[0] - new_width) // 2
    center_y = (bg.size[1] - new_height) // 2
    
    x_pos = center_x + x_offset
    y_pos = center_y - y_offset
    
    result = bg.copy()
    result.paste(fg, (x_pos, y_pos), fg)
    
    return result

def reset_controls():
    return 100, 0, 0, 0, False, False

def calculate_fit_scale(fg_image, bg_image):
    """Calculate scale percentage to fit foreground within background"""
    if not (fg_image and bg_image):
        return 100
        
    # Get image sizes
    if isinstance(fg_image, str):
        fg_image = Image.open(fg_image)
    if isinstance(bg_image, str):
        bg_image = Image.open(bg_image)
    
    # Calculate ratios
    width_ratio = bg_image.width / fg_image.width
    height_ratio = bg_image.height / fg_image.height
    
    # Use the smaller ratio to ensure fit
    fit_ratio = min(width_ratio, height_ratio)
    
    # Convert to percentage, with a small margin
    return int(fit_ratio * 95)  # 95% of perfect fit to leave a margin


def adjust_color_temperature(image, temperature):
    """Adjust color temperature of an image (negative=cool, positive=warm)"""
    # Convert to numpy array for processing
    img_array = np.array(image)
    
    # Separate the alpha channel if it exists
    has_alpha = img_array.shape[-1] == 4
    if has_alpha:
        img_rgb = img_array[..., :3]
        alpha = img_array[..., 3]
    else:
        img_rgb = img_array
    
    # Adjust temperature by modifying RGB channels
    if temperature > 0:  # Warmer
        img_rgb[..., 2] = np.clip(img_rgb[..., 2] + temperature, 0, 255)  # More red
        img_rgb[..., 0] = np.clip(img_rgb[..., 0] - temperature/2, 0, 255)  # Less blue
    else:  # Cooler
        img_rgb[..., 0] = np.clip(img_rgb[..., 0] - temperature, 0, 255)  # More blue
        img_rgb[..., 2] = np.clip(img_rgb[..., 2] + temperature/2, 0, 255)  # Less red
    
    # Recombine with alpha if necessary
    if has_alpha:
        img_array = np.dstack((img_rgb, alpha))
    else:
        img_array = img_rgb
    
    return Image.fromarray(img_array.astype('uint8'))


def apply_color_adjustments(image, brightness=1.0, contrast=1.0, saturation=1.0, 
                          temperature=0, tint_color=None, tint_strength=0):
    """Apply color adjustments to an image while preserving transparency"""
    if image is None:
        return None
        
    # Store original alpha channel
    alpha = None
    if image.mode == 'RGBA':
        alpha = image.split()[3]
    
    # Convert to RGB for adjustments
    img = image.convert('RGB')
    
    # Apply basic adjustments
    if brightness != 1.0:
        img = ImageEnhance.Brightness(img).enhance(brightness)
    if contrast != 1.0:
        img = ImageEnhance.Contrast(img).enhance(contrast)
    if saturation != 1.0:
        img = ImageEnhance.Color(img).enhance(saturation)
    if temperature != 0:
        img = adjust_color_temperature(img, temperature)
    
    # Apply tint if specified
    if tint_color and tint_strength > 0:
        tint_layer = Image.new('RGB', img.size, tint_color)
        img = Image.blend(img, tint_layer, tint_strength)
    
    # Restore alpha channel if it existed
    if alpha:
        img.putalpha(alpha)
    
    return img

    
def reset_color_controls():
    """Reset all color grading controls to default values"""
    return 1.0, 1.0, 1.0, 0, "#000000", 0

    
def save_combined(image):
    if image is None:
        return update_gallery(), "⚠️ No image to save"
        
    if isinstance(image, np.ndarray):
        image = Image.fromarray(image)
        
    output_path = os.path.join(output_folder, generate_filename("combined"))
    image.save(output_path)
    return update_gallery(), f"✅ Saved image: {os.path.basename(output_path)}"


css = """
/* Specific adjustments for Image */
.image-container .image-custom {
    max-width: 100% !important;
    max-height: 80vh !important;
    width: auto !important;
    height: auto !important;
}

/* Center the preview row */
.preview-row {
    display: flex !important;
    justify-content: center !important;
    width: 100% !important;
}

/* Center the ImageSlider container and maintain full width for slider */
.image-container .image-slider-custom {
    display: flex !important;
    justify-content: center !important;
    align-items: center !important;
    width: 100% !important;
}

/* Style for the slider container */
.image-container .image-slider-custom > div {
    width: 100% !important;
    max-width: 100% !important;
    max-height: 80vh !important;
}

/* Ensure both before/after images maintain aspect ratio */
.image-container .image-slider-custom img {
    max-height: 80vh !important;
    width: 100% !important;
    height: auto !important;
    object-fit: contain !important;
}

/* Style for the slider handle */
.image-container .image-slider-custom .image-slider-handle {
    width: 2px !important;
    background: white !important;
    border: 2px solid rgba(0, 0, 0, 0.6) !important;
}
"""

    
# Create interface
with gr.Blocks(css=css) as demo:
    # gr.Markdown("# Background Removal & Replacement")
    # Shared gallery component outside of tabs
    with gr.Column():
        shared_gallery = gr.Gallery(
            label="image gallery",
            columns=5,
            rows=3,
            height="auto",
            allow_preview=True,                
            preview=True, 
            object_fit="scale-down",
            value=update_gallery()
        )
    
    with gr.Tabs() as tabs:
        with gr.Tab("Quick Remove"):
            with gr.Row():
                with gr.Column(elem_classes="image-container"):
                    image = gr.Image(
                        type="pil",
                        label="Input Image",
                        elem_classes=["image-custom"]
                    )
                   
                with gr.Column(elem_classes="image-container"):
                    slider1 = ImageSlider(
                        interactive=False,
                        label="Before / After",
                        elem_classes=["image-slider-custom"]
                    )
                    
            with gr.Row():
                with gr.Column():
                    # Add URL input with improved help text
                    url_input = gr.Textbox(
                        label="Image URL (optional)",
                        placeholder="Enter image URL (must contain .jpg, .png, .gif, or .webp)",
                        info="💡 Paste a direct link to an image. Right-click an image online and select 'Copy image address'"
                    )
                with gr.Column():    
                    status_text = gr.Textbox(label=None, interactive=False, show_label=False, container=False)
                    open_folder_btn = gr.Button("📂 Open Output Folder", size="sm")
                    
            # Update event handlers
            url_input.submit(fn, inputs=url_input, outputs=[slider1, shared_gallery, status_text])
            image.change(fn, inputs=image, outputs=[slider1, shared_gallery, status_text])
            open_folder_btn.click(open_output_folder, outputs=status_text)
        
        with gr.Tab("Process & Replace"):
            with gr.Row():
                with gr.Column(elem_classes="image-container"):
                    selected_fg = gr.Image(type="pil", label="Processed Image", elem_classes=["image-custom"])
                with gr.Column(elem_classes="image-container"):
                    bg_image = gr.Image(type="pil", label="Background Image", elem_classes=["image-custom"])

            with gr.Row(elem_classes="preview-row"):        
                with gr.Column(elem_classes="image-container"):
                    # gr.Markdown("### Adjust Size and Position")
                    preview_image = gr.Image(type="pil", label="Combined Image", elem_classes=["image-custom"])
            with gr.Accordion("Placement Controls"):
                with gr.Row():
                    with gr.Column():
                        scale_slider = gr.Slider(
                            minimum=1,
                            maximum=200,
                            value=100,
                            label="Size %",
                            info="Adjust the size of your image"
                        )
                        rotation = gr.Slider(
                            minimum=-180,
                            maximum=180,
                            value=0,
                            step=1,
                            label="Rotate",
                            info="Rotate image (degrees)"
                        )

                    with gr.Column():    
                        x_offset = gr.Slider(
                            minimum=-1000,
                            maximum=1000,
                            value=0,
                            step=1,
                            label="Move Left/Right",
                            info="Negative values move left, positive move right"
                        )
                        y_offset = gr.Slider(
                            minimum=-1000,
                            maximum=1000,
                            value=0,
                            step=1,
                            label="Move Up/Down",
                            info="Right to move up, left to move down"
                        )
                with gr.Row():
                    flip_h = gr.Checkbox(
                        label="Flip Horizontally",
                        value=False,
                        info="Mirror the image horizontally"
                    )
                    flip_v = gr.Checkbox(
                        label="Flip Vertically",
                        value=False,
                        info="Mirror the image vertically"
                    )
                with gr.Row():
                    gr.Button("↺ Reset Placement", size="sm").click(
                        reset_controls,
                        outputs=[scale_slider, x_offset, y_offset, rotation, flip_h, flip_v]
                    )
                    gr.Button("↔ Fit to BG", size="sm").click(
                        lambda fg, bg: calculate_fit_scale(fg, bg),
                        inputs=[selected_fg, bg_image],
                        outputs=scale_slider
                    )        
                
                
            with gr.Accordion("Color Grading - basic, no substitute for a real image editor!"):  
                with gr.Row():                
                    with gr.Column(scale=1):
                        brightness_slider = gr.Slider(
                            minimum=0.0, maximum=2.0, value=1.0, step=0.05,
                            label="Brightness", info="Adjust image brightness"
                        )
                        contrast_slider = gr.Slider(
                            minimum=0.0, maximum=2.0, value=1.0, step=0.05,
                            label="Contrast", info="Adjust image contrast"
                        )
                        saturation_slider = gr.Slider(
                            minimum=0.0, maximum=2.0, value=1.0, step=0.05,
                            label="Saturation", info="Adjust color intensity"
                        )
                    with gr.Column(scale=1):
                        tint_color = gr.ColorPicker(
                            label="Tint Color", 
                            info="Choose a color to overlay"
                        )
                        tint_strength = gr.Slider(
                            minimum=0.0, maximum=1.0, value=0.0, step=0.05,
                            label="Tint Strength", info="Adjust tint opacity"
                        )   
                        temperature_slider = gr.Slider(
                            minimum=-50, maximum=50, value=0, step=1,
                            label="Temperature", info="Adjust warm/cool color balance"
                        )
                      
                        
                with gr.Row():
                    reset_color_btn = gr.Button("↺ Reset Colors", size="sm")

                    reset_color_btn.click(
                        reset_color_controls,
                        outputs=[brightness_slider, contrast_slider, saturation_slider,
                                temperature_slider, tint_color, tint_strength]
                    )
            
            with gr.Row():
                with gr.Column():
                    save_btn = gr.Button("💾 Save Image", variant="primary", size="sm")
                    open_folder_btn = gr.Button("📂 Open Output Folder", size="sm")
                status_text = gr.Textbox(label=None, interactive=False, show_label=False, container=False)
                
                open_folder_btn.click(
                    open_output_folder,
                    outputs=status_text
                )        
                
            def update_preview(fg, bg, scale, x, y, rotation, flip_h, flip_v, 
                              brightness, contrast, saturation, temperature, 
                              tint_color, tint_strength):
                if not fg or not bg:
                    return None
                return combine_images(
                    fg, bg, scale, x, y, flip_h, flip_v, rotation,
                    brightness, contrast, saturation, temperature, 
                    tint_color, tint_strength
                )
            
            color_controls = [
                brightness_slider, contrast_slider, saturation_slider,
                temperature_slider, tint_color, tint_strength
            ]
            
            all_controls = [
                selected_fg, bg_image, scale_slider, x_offset, y_offset,
                rotation, flip_h, flip_v, *color_controls
            ]
            
            for control in all_controls:
                control.change(
                    update_preview,
                    inputs=all_controls,
                    outputs=preview_image
                )
                
            # When a new foreground image is loaded
            selected_fg.change(
                lambda: (
                    # Reset all controls to default values
                    *reset_controls(),  # Returns (100, 0, 0, 0, False, False)
                    *reset_color_controls(),  # Returns (1.0, 1.0, 1.0, 0, "#000000", 0)
                ),
                outputs=[
                    scale_slider, x_offset, y_offset, rotation, flip_h, flip_v,
                    brightness_slider, contrast_slider, saturation_slider,
                    temperature_slider, tint_color, tint_strength
                ]
            )
                
                
                
                
        with gr.Tab("Batch Processing"):
            gr.Markdown("""
            ### 🎯 Batch Background Removal

            Upload multiple images to process them all at once.

            - Two ways to upload multiple files:
                - Drag & drop files 
                - Click the file upload window below and hold ctrl+click (or ⌘+click on Mac) to select multiple files
            - Clear files via 'x' button in upload window.
            """)
            
            with gr.Row():
                file_output = gr.File(
                    file_count="multiple",
                    label="Upload Images",
                    file_types=["image"],
                    scale=2
                )
            
            with gr.Row():
                with gr.Column(scale=1):
                    process_button = gr.Button("🚀 Process All Images", variant="primary")
                    status = gr.Textbox(label="Status", lines=4)
                    gr.Markdown("""
                    #### Tips:
                    - Processed images will appear in the gallery above
                    - Original filenames will be preserved with '_nobg' suffix
                    - Supported formats: JPG, PNG, WEBP
                    """)
                    open_folder_btn = gr.Button("📂 Open Output Folder", size="sm")
        
        process_button.click(
            batch_process_images,
            inputs=[file_output],
            outputs=[status, shared_gallery]
        )
        save_btn.click(
            save_combined,
            inputs=[preview_image],
            outputs=[shared_gallery, status_text]
        )
        open_folder_btn.click(
            open_output_folder,
            outputs=status_text
        )

    # with gr.Tab("From URL"):
        # text = gr.Textbox(label="Paste an image URL")
        # slider2 = ImageSlider(label="Before/After", type="pil")
        # output_file2 = gr.File(label="Download PNG")
        # text.submit(fn, inputs=text, outputs=[slider2, output_file2])

        
demo.launch(share=False)