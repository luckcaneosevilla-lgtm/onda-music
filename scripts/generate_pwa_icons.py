"""Generate original IVOX letter/wave icons; Pillow is needed only to regenerate."""
from pathlib import Path
import math
from PIL import Image,ImageDraw,ImageFont
root=Path(__file__).resolve().parents[1]
for size in (192,512):
    scale=3;n=size*scale
    image=Image.new('RGB',(n,n),'black');draw=ImageDraw.Draw(image)
    try:font=ImageFont.truetype('C:/Windows/Fonts/segoeuib.ttf',int(n*.235))
    except OSError:font=ImageFont.truetype('DejaVuSans-Bold.ttf',int(n*.235))
    draw.text((n/2,n*.44),'IVOX',font=font,fill='white',anchor='mm')
    for row in range(2):
        points=[(int(n*(.25+i*.5/160)),int(n*(.62+row*.065+math.sin(i/160*math.pi*4)*.022))) for i in range(161)]
        draw.line(points,fill='#82b6ff',width=max(2,int(n*.014)))
    image.resize((size,size),Image.Resampling.LANCZOS).save(root/'web'/f'icon-{size}x{size}.png')
print('Iconos IVOX generados: 192x192 y 512x512.')
