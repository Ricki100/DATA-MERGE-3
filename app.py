from flask import Flask, render_template, request, jsonify, send_file, url_for
from werkzeug.utils import secure_filename
from flask_cors import CORS
import os
import pandas as pd
from PIL import Image, ImageDraw, ImageFont
import json
import io
import zipfile
from datetime import datetime
import tempfile
import shutil
from io import BytesIO
import requests

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes
app.config['UPLOAD_FOLDER'] = os.path.join('static', 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# Ensure required directories exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(os.path.join('static', 'previews'), exist_ok=True)
os.makedirs(os.path.join('static', 'fonts'), exist_ok=True)

# Font management
FONTS_DIR = os.path.join('static', 'fonts')
DEFAULT_FONT = os.path.join(FONTS_DIR, 'arial.ttf')

# Ensure fonts directory exists
os.makedirs(FONTS_DIR, exist_ok=True)

def get_font_path(font_name, bold=False, italic=False):
    """Get the appropriate font file path based on name and style."""
    # Since we only have arial.ttf, we'll use it and simulate bold/italic
    return DEFAULT_FONT

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload_template', methods=['POST'])
def upload_template():
    if 'template' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    
    file = request.files['template']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)
    
    # Return the URL for the uploaded image
    image_url = url_for('static', filename=f'uploads/{filename}')
    return jsonify({
        'filename': filename,
        'image_url': image_url,
        'message': 'Template uploaded successfully'
    })

@app.route('/upload_csv', methods=['POST'])
def upload_csv():
    if 'csv' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    
    file = request.files['csv']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    try:
        # Check file extension to determine if it's CSV or Excel
        if file.filename.lower().endswith('.csv'):
            # For CSV files, use encoding='utf-8-sig' to handle BOM and other encoding issues
            df = pd.read_csv(file, encoding='utf-8-sig', on_bad_lines='skip')
        elif file.filename.lower().endswith(('.xlsx', '.xls')):
            # For Excel files
            df = pd.read_excel(file)
        else:
            return jsonify({'error': 'Unsupported file format. Please upload a CSV or Excel file'}), 400
        
        preview_rows = min(20, len(df))  # Show up to 20 rows in preview
        return jsonify({
            'columns': df.columns.tolist(),
            'preview': df.head(preview_rows).to_dict('records'),
            'all_data': df.to_dict('records'),  # Send all rows
            'total_rows': len(df)
        })
    except Exception as e:
        return jsonify({'error': f"Error reading file: {str(e)}"}), 400

def wrap_text_to_width(draw, text, font, max_width):
    """Helper function to wrap text based on given width"""
    words = text.split()
    lines = []
    current_line = []
    
    if not words:
        return []
    
    for word in words:
        # Try adding the word to the current line
        test_line = current_line + [word]
        test_width = draw.textlength(' '.join(test_line), font=font)
        
        if test_width <= max_width:
            current_line.append(word)
        else:
            # If current line has words, add it to lines
            if current_line:
                lines.append(' '.join(current_line))
                current_line = [word]
            else:
                # If a single word is too long, split it
                chars = list(word)
                current_chars = []
                for char in chars:
                    test_chars = current_chars + [char]
                    if draw.textlength(''.join(test_chars), font=font) <= max_width:
                        current_chars.append(char)
                    else:
                        if current_chars:
                            lines.append(''.join(current_chars))
                            current_chars = [char]
                        else:
                            lines.append(char)
                if current_chars:
                    current_line = [''.join(current_chars)]
    
    # Add the last line if there's anything left
    if current_line:
        lines.append(' '.join(current_line))
    
    return lines

def draw_text_box(draw, box, text, img_width, img_height):
    """Helper function to draw text box with proper wrapping and alignment"""
    try:
        # Get font size and validate
        font_size = int(box.get('fontSize', 24))
        if font_size < 8:
            font_size = 8
        elif font_size > 200:
            font_size = 200
            
        # Get font family and style
        font_family = box.get('fontFamily', 'Arial')
        
        # Convert string 'true'/'false' to boolean
        def str_to_bool(val):
            if isinstance(val, bool):
                return val
            return str(val).lower() == 'true'
        
        bold = str_to_bool(box.get('bold', False))
        italic = str_to_bool(box.get('italic', False))
        underline = str_to_bool(box.get('underline', False))
        
        # Get the font and apply stroke for bold simulation if needed
        font = ImageFont.truetype(DEFAULT_FONT, font_size)
        stroke_width = 0
        if bold:
            stroke_width = max(1, font_size // 30)  # Scale stroke width with font size
        
        # Get box dimensions and position
        x = float(box.get('x', 0))
        y = float(box.get('y', 0))
        box_width = float(box.get('width', img_width - x))
        box_height = float(box.get('height', img_height - y))
        
        # Validate color
        color = box.get('color', '#000000')
        if not color.startswith('#'):
            color = '#000000'
        # Convert color from hex to RGB
        color = tuple(int(color.lstrip('#')[i:i+2], 16) for i in (0, 2, 4))
        
        # Wrap text to fit box width
        lines = wrap_text_to_width(draw, text, font, box_width - (stroke_width * 2 if bold else 0))
        
        # Calculate line height and total text height
        line_spacing = font_size * 1.2
        total_height = len(lines) * line_spacing
        
        # Adjust starting y position to respect vertical alignment
        if total_height < box_height:
            y = y + (box_height - total_height) // 2
        
        # Draw each line with proper alignment
        current_y = y
        for line in lines:
            # Calculate line width for alignment
            bbox = draw.textbbox((0, 0), line, font=font)
            line_width = bbox[2] - bbox[0]
            
            # Calculate x position based on alignment
            line_x = x
            align = box.get('align', 'left')
            
            if align == 'center':
                line_x = x + (box_width - line_width) // 2
            elif align == 'right':
                line_x = x + box_width - line_width
            
            # Draw the line with stroke for bold simulation
            if bold:
                # Draw the stroke
                draw.text((line_x, current_y), line, font=font, fill=color, stroke_width=stroke_width, stroke_fill=color)
            else:
                draw.text((line_x, current_y), line, font=font, fill=color)
            
            # Draw underline if specified
            if underline:
                underline_y = current_y + font_size
                underline_width = max(1, font_size // 20)
                if bold:
                    underline_width = max(underline_width, stroke_width)
                draw.line([(line_x, underline_y), (line_x + line_width, underline_y)],
                         fill=color, width=underline_width)
            
            current_y += line_spacing
            
            # Stop if we exceed box height
            if current_y - y > box_height:
                break
                
    except Exception as e:
        print(f"Error drawing text box: {str(e)}")
        # Draw a red rectangle to indicate error
        draw.rectangle([x, y, x + box_width, y + box_height], outline='red', width=2)
        draw.text((x + 10, y + box_height/2), f"Error: {str(e)[:50]}...", fill='red')

def draw_image_box(draw, box, image_url, img_width, img_height):
    """Helper function to draw image from URL into a box"""
    try:
        # Get box dimensions and position
        x = float(box['x'])
        y = float(box['y'])
        box_width = float(box.get('width', img_width - x))
        box_height = float(box.get('height', img_height - y))
        
        try:
            # Download and open the image from URL
            response = requests.get(image_url, timeout=5)
            if response.status_code == 200:
                overlay_img = Image.open(BytesIO(response.content))
                
                # Calculate dimensions while maintaining aspect ratio
                overlay_width, overlay_height = overlay_img.size
                scale = min(box_width/overlay_width, box_height/overlay_height)
                new_width = int(overlay_width * scale)
                new_height = int(overlay_height * scale)
                
                # Resize the overlay image
                overlay_img = overlay_img.resize((new_width, new_height), Image.Resampling.LANCZOS)
                
                # Calculate position to center the image in the box
                paste_x = int(x + (box_width - new_width) / 2)
                paste_y = int(y + (box_height - new_height) / 2)
                
                # If the overlay has transparency, use it as mask
                if overlay_img.mode in ('RGBA', 'LA'):
                    # Extract the alpha channel as mask
                    mask = overlay_img.split()[-1] if overlay_img.mode == 'RGBA' else overlay_img.split()[1]
                    # Convert to RGB for pasting
                    overlay_img = overlay_img.convert('RGB')
                    return (overlay_img, (paste_x, paste_y), mask)
                else:
                    return (overlay_img, (paste_x, paste_y))
                    
        except Exception as e:
            print(f"Error processing image URL: {str(e)}")
            # Draw an error placeholder
            draw.rectangle([x, y, x + box_width, y + box_height], outline='red', width=2)
            draw.text((x + 10, y + box_height/2), f"Image Error: {str(e)[:50]}...", fill='red')
            
    except Exception as e:
        print(f"Error drawing image box: {str(e)}")

@app.route('/preview_images', methods=['POST'])
def preview_images():
    try:
        data = request.get_json()
        template_path = os.path.join(app.config['UPLOAD_FOLDER'], data['template'])
        csv_data = data['csv_data']
        text_boxes = data['text_boxes']
        
        # Create previews directory if it doesn't exist
        preview_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'previews')
        os.makedirs(preview_dir, exist_ok=True)
        
        # Clear any existing preview files
        for file in os.listdir(preview_dir):
            if file.startswith('preview_') and file.endswith('.png'):
                try:
                    os.remove(os.path.join(preview_dir, file))
                except Exception as e:
                    print(f"Warning: Could not remove old preview file: {e}")
        
        preview_urls = []
        
        for i, row in enumerate(csv_data):
            try:
                # Open and process the template image
                with Image.open(template_path) as img:
                    # Convert to RGB if necessary
                    if img.mode != 'RGB':
                        img = img.convert('RGB')
                    
                    draw = ImageDraw.Draw(img)
                    img_width, img_height = img.size
                    
                    # Draw text boxes and images
                    for box in text_boxes:
                        content = str(row[box['column']])
                        if box.get('isImage', False):
                            # Handle image box
                            result = draw_image_box(draw, box, content, img_width, img_height)
                            if result:
                                if len(result) == 3:
                                    overlay, pos, mask = result
                                    img.paste(overlay, pos, mask)
                                else:
                                    overlay, pos = result
                                    img.paste(overlay, pos)
                        else:
                            # Handle text box
                            draw_text_box(draw, box, content, img_width, img_height)
                    
                    # Save the preview image
                    preview_filename = f'preview_{i}.png'
                    preview_path = os.path.join(preview_dir, preview_filename)
                    
                    # Ensure the directory exists
                    os.makedirs(os.path.dirname(preview_path), exist_ok=True)
                    
                    # Save with explicit format and quality
                    img.save(
                        preview_path,
                        format='PNG',
                        optimize=True,
                        quality=95
                    )
                    
                    # Verify the file was created and is readable
                    if os.path.exists(preview_path) and os.access(preview_path, os.R_OK):
                        preview_urls.append(url_for('static', filename=f'uploads/previews/{preview_filename}'))
                    else:
                        raise Exception(f"Failed to save or access preview file: {preview_filename}")
                        
            except Exception as e:
                print(f"Error processing preview {i}: {str(e)}")
                continue
        
        if not preview_urls:
            return jsonify({'error': 'No preview images were generated successfully'}), 500
        
        return jsonify({
            'preview_urls': preview_urls,
            'message': f'Generated {len(preview_urls)} preview images'
        })
        
    except Exception as e:
        print(f"Error in preview_images: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/download_previews', methods=['POST'])
def download_previews():
    try:
        data = request.get_json()
        preview_urls = data['preview_urls']
        
        # Create a BytesIO object to store the zip file
        memory_file = BytesIO()
        
        # Create the zip file with proper compression
        with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
            preview_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'previews')
            
            # Add each preview image to the zip file
            for i, url in enumerate(preview_urls):
                preview_filename = f'preview_{i}.png'
                preview_path = os.path.join(preview_dir, preview_filename)
                
                # Verify file exists and is readable
                if os.path.exists(preview_path) and os.access(preview_path, os.R_OK):
                    # Read the image file in binary mode
                    with open(preview_path, 'rb') as img_file:
                        img_data = img_file.read()
                        # Write the image data directly to the zip
                        zf.writestr(preview_filename, img_data)
        
        # Reset the pointer to the beginning of the BytesIO object
        memory_file.seek(0)
        
        # Create the response with the zip file
        response = send_file(
            memory_file,
            mimetype='application/zip',
            as_attachment=True,
            download_name='preview_images.zip'
        )
        
        # Add headers to prevent caching and ensure proper download
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        response.headers['Content-Disposition'] = 'attachment; filename=preview_images.zip'
        
        return response
            
    except Exception as e:
        print(f"Error in download_previews: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/generate_images', methods=['POST'])
def generate_images():
    try:
        data = request.get_json()
        # Construct the full path to the template in the uploads directory
        template_path = os.path.join(app.config['UPLOAD_FOLDER'], data['template'])
        csv_data = data['csv_data']
        text_boxes = data['text_boxes']
        
        # Verify template file exists
        if not os.path.exists(template_path):
            return jsonify({'error': f'Template file not found: {template_path}'}), 404
        
        # Create a temporary directory for the generated images
        temp_dir = tempfile.mkdtemp()
        zip_path = os.path.join(temp_dir, 'generated_images.zip')
        
        # Create a ZIP file
        with zipfile.ZipFile(zip_path, 'w') as zipf:
            for i, row in enumerate(csv_data):
                img = Image.open(template_path)
                draw = ImageDraw.Draw(img)
                img_width, img_height = img.size
                
                for box in text_boxes:
                    text = str(row[box['column']])
                    draw_text_box(draw, box, text, img_width, img_height)
                
                # Save the image to the ZIP file
                img_path = f'image_{i+1}.png'
                img_buffer = BytesIO()
                img.save(img_buffer, format='PNG')
                zipf.writestr(img_path, img_buffer.getvalue())
        
        # Return the ZIP file
        return send_file(zip_path, as_attachment=True, download_name='generated_images.zip')
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500
        
    finally:
        # Clean up temporary directory
        if 'temp_dir' in locals():
            shutil.rmtree(temp_dir)

def wrap_text(draw, text, font, max_width):
    """Wrap text to fit within a given width."""
    words = text.split()
    lines = []
    current_line = []
    
    for word in words:
        # Try adding the word to the current line
        test_line = current_line + [word]
        test_text = ' '.join(test_line)
        bbox = draw.textbbox((0, 0), test_text, font=font)
        test_width = bbox[2] - bbox[0]
        
        if test_width <= max_width:
            current_line.append(word)
        else:
            if current_line:
                lines.append(' '.join(current_line))
                current_line = [word]
            else:
                lines.append(word)
    
    if current_line:
        lines.append(' '.join(current_line))
    
    return lines

@app.route('/generate_text_images', methods=['POST'])
def generate_text_images():
    try:
        data = request.get_json()
        text_boxes = data.get('text_boxes', [])
        csv_data = data.get('csv_data', [])
        
        if not text_boxes or not csv_data:
            return jsonify({'error': 'Missing text boxes or CSV data'}), 400
            
        # Create previews directory if it doesn't exist
        os.makedirs('static/previews', exist_ok=True)
        
        # Clear existing previews
        for file in os.listdir('static/previews'):
            if file.endswith('.png'):
                os.remove(os.path.join('static/previews', file))
        
        preview_urls = []
        
        for i, row in enumerate(csv_data):
            # Create a new image for each row
            img = Image.new('RGBA', (800, 600), (255, 255, 255, 255))  # White background
            draw = ImageDraw.Draw(img)
            
            for text_box in text_boxes:
                column = text_box.get('column')
                if column in row:
                    text = str(row[column])
                    # Use the consolidated draw_text_box function
                    draw_text_box(draw, text_box, text, 800, 600)
            
            # Save the image
            filename = f'preview_{i+1}.png'
            filepath = os.path.join('static/previews', filename)
            img.save(filepath)
            preview_urls.append(f'/static/previews/{filename}')
        
        return jsonify({'preview_urls': preview_urls})
        
    except Exception as e:
        print(f"Error generating images: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/text_only')
def text_only():
    return render_template('text_only.html')

@app.route('/download_text_previews', methods=['POST'])
def download_text_previews():
    try:
        data = request.get_json()
        preview_urls = data['preview_urls']
        
        # Create a BytesIO object to store the zip file
        memory_file = BytesIO()
        
        # Create the zip file with proper compression
        with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
            preview_dir = os.path.join('static', 'previews')
            
            # Add each preview image to the zip file
            for i, url in enumerate(preview_urls):
                # Extract filename from URL
                filename = os.path.basename(url)
                filepath = os.path.join(preview_dir, filename)
                
                # Verify file exists and is readable
                if os.path.exists(filepath) and os.access(filepath, os.R_OK):
                    # Read the image file in binary mode
                    with open(filepath, 'rb') as img_file:
                        img_data = img_file.read()
                        # Write the image data to the zip with a sequential name
                        zf.writestr(f'image_{i+1}.png', img_data)
        
        # Reset the pointer to the beginning of the BytesIO object
        memory_file.seek(0)
        
        # Create the response with the zip file
        response = send_file(
            memory_file,
            mimetype='application/zip',
            as_attachment=True,
            download_name='text_previews.zip'
        )
        
        # Add headers to prevent caching
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        
        return response
            
    except Exception as e:
        print(f"Error in download_text_previews: {str(e)}")
        return jsonify({'error': str(e)}), 500

# Add new routes for image-only functionality
@app.route('/upload_image_template', methods=['POST'])
def upload_image_template():
    """Dedicated endpoint for uploading templates in the image-only tab"""
    if 'template' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    
    file = request.files['template']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)
    
    # Return the URL for the uploaded image
    image_url = url_for('static', filename=f'uploads/{filename}')
    return jsonify({
        'filename': filename,
        'image_url': image_url,
        'message': 'Template uploaded successfully'
    })

@app.route('/upload_image_csv', methods=['POST'])
def upload_image_csv():
    """Dedicated endpoint for uploading CSV in the image-only tab"""
    if 'csv' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    
    file = request.files['csv']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    try:
        # Check file extension to determine if it's CSV or Excel
        if file.filename.lower().endswith('.csv'):
            # For CSV files, use encoding='utf-8-sig' to handle BOM and other encoding issues
            df = pd.read_csv(file, encoding='utf-8-sig', on_bad_lines='skip')
        elif file.filename.lower().endswith(('.xlsx', '.xls')):
            # For Excel files
            df = pd.read_excel(file)
        else:
            return jsonify({'error': 'Unsupported file format. Please upload a CSV or Excel file'}), 400
        
        preview_rows = min(20, len(df))  # Show up to 20 rows in preview
        return jsonify({
            'columns': df.columns.tolist(),
            'preview': df.head(preview_rows).to_dict('records'),
            'all_data': df.to_dict('records'),  # Send all rows
            'total_rows': len(df)
        })
    except Exception as e:
        return jsonify({'error': f"Error reading file: {str(e)}"}), 400

# Add a hybrid route for the combined generator
@app.route('/preview_combined_images', methods=['POST'])
def preview_combined_images():
    """Process both text and image boxes in a single template"""
    data = request.get_json()
    
    if not data:
        return jsonify({'error': 'No data received'}), 400
    
    template_filename = data.get('template')
    csv_data = data.get('csv_data', [])
    boxes = data.get('text_boxes', [])
    
    if not template_filename or not csv_data or not boxes:
        return jsonify({'error': 'Missing required parameters'}), 400
    
    try:
        template_path = os.path.join(app.config['UPLOAD_FOLDER'], template_filename)
        if not os.path.exists(template_path):
            return jsonify({'error': f'Template file not found: {template_filename}'}), 404
        
        template_img = Image.open(template_path)
        
        # Generate preview images
        preview_urls = []
        max_previews = min(5, len(csv_data))  # Limit preview generation
        
        for idx, row in enumerate(csv_data[:max_previews]):
            # Create a copy of template for each row
            img = template_img.copy()
            draw = ImageDraw.Draw(img)
            
            # Process each box (can be text or image)
            for box in boxes:
                column = box.get('column')
                
                if column not in row:
                    continue
                
                x = float(box.get('x', 0))
                y = float(box.get('y', 0))
                width = float(box.get('width', 100))
                height = float(box.get('height', 100))
                
                # Check if it's an image box
                if box.get('isImage', False):
                    if row[column]:  # Only process if URL is provided
                        try:
                            draw_image_box(draw, box, row[column], img.width, img.height)
                        except Exception as e:
                            print(f"Error drawing image from {row[column]}: {str(e)}")
                            # Draw error box
                            draw.rectangle([x, y, x + width, y + height], outline='red', width=2)
                            draw.text((x + 5, y + 5), f"Image Error: {str(e)[:30]}...", fill='red')
                else:
                    # It's a text box
                    if row[column]:  # Only draw if text is provided
                        draw_text_box(draw, box, row[column], img.width, img.height)
            
            # Save preview image
            preview_filename = f'preview_{idx}_{int(datetime.now().timestamp())}.png'
            preview_path = os.path.join('static', 'previews', preview_filename)
            img.save(preview_path)
            
            # Add URL to list
            preview_url = url_for('static', filename=f'previews/{preview_filename}')
            preview_urls.append(preview_url)
        
        return jsonify({
            'preview_urls': preview_urls,
            'message': f'Generated {len(preview_urls)} preview images'
        })
        
    except Exception as e:
        print(f"Error generating preview: {str(e)}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True) 