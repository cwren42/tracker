from PIL import Image, ImageDraw

# Create 32x32 favicon
img = Image.new('RGBA', (32, 32), (0, 0, 0, 0))
draw = ImageDraw.Draw(img)

# Background circle
draw.ellipse([2, 2, 30, 30], fill='#0d6efd', outline='white', width=1)

# Laptop screen (white rectangle)
draw.rectangle([8, 10, 24, 19], fill='white')

# Laptop base (trapezoid approximation)
draw.polygon([(6, 20), (7, 22), (25, 22), (26, 20)], fill='white')

# Checkmark
draw.line([(12, 14), (14, 17), (20, 12)], fill='#0d6efd', width=2)

# Save as PNG
img.save('static/favicon-32.png')
print("Created static/favicon-32.png")

# Create 16x16 version (simplified)
img16 = Image.new('RGBA', (16, 16), (0, 0, 0, 0))
draw16 = ImageDraw.Draw(img16)

draw16.ellipse([1, 1, 15, 15], fill='#0d6efd', outline='white')
draw16.rectangle([4, 5, 12, 10], fill='white')
draw16.line([(6, 7), (7, 9), (10, 6)], fill='#0d6efd', width=1)

img16.save('static/favicon-16.png')
print("Created static/favicon-16.png")

# Create 192x192 for Android
img192 = Image.new('RGBA', (192, 192), (0, 0, 0, 0))
draw192 = ImageDraw.Draw(img192)

draw192.ellipse([12, 12, 180, 180], fill='#0d6efd', outline='white', width=4)
draw192.rectangle([48, 60, 144, 120], fill='white')
draw192.polygon([(36, 120), (42, 132), (150, 132), (156, 120)], fill='white')
draw192.line([(72, 84), (84, 102), (120, 72)], fill='#0d6efd', width=8)

img192.save('static/favicon-192.png')
print("Created static/favicon-192.png")

