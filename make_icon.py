import sys
import os
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QImage

# 1. Initialize Qt (Required to load image plugins)
app = QApplication(sys.argv)

# 2. Settings
input_file = "app_icon.png"
output_file = "app_icon.ico"

print(f"Looking for {input_file}...")

if not os.path.exists(input_file):
    print(f"❌ Error: File '{input_file}' not found.")
    print("Please rename your image to 'app_icon.png' and place it in this folder.")
else:
    # 3. Load and Convert
    image = QImage(input_file)
    if image.isNull():
        print("❌ Error: The image file is corrupted or not a valid image.")
    else:
        # Scale to 256x256 (Standard Windows Icon Size) for best quality
        image = image.scaled(256, 256)
        if image.save(output_file, "ICO"):
            print(f"✅ Success! Created '{output_file}'.")
            print("You can now run the PyInstaller command again.")
        else:
            print("❌ Error: Could not save ICO file.")