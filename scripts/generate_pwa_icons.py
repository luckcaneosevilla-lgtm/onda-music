"""Generate the IVOX Eye of Horus icons (gold on black); Pillow is needed only to regenerate.

The strokes are the same paths as the LOGO SVG in web/app.js (64x44 viewBox),
sampled as cubic Béziers and drawn supersampled for smooth edges.
"""
from pathlib import Path
from PIL import Image,ImageDraw,ImageFilter
root=Path(__file__).resolve().parents[1]
GOLD=(212,175,55);BG=(10,10,12);LAPIS=(30,58,138)

def cubic(p0,p1,p2,p3,steps=400):
    return [tuple((1-t)**3*a+3*(1-t)**2*t*b+3*(1-t)*t**2*c+t**3*d for a,b,c,d in zip(p0,p1,p2,p3)) for t in (i/steps for i in range(steps+1))]

STROKES=[
    cubic((6,9),(20,2),(44,2),(58,8)),
    cubic((4,20),(18,8),(46,8),(60,20))+cubic((60,20),(46,31),(18,31),(4,20)),
    [(27,28),(24,41)],
    cubic((38,28),(42,36),(50,39),(56,35))+cubic((56,35),(59,32),(56,29),(53,31)),
]

def eye(n,scale,ox,oy,color,width):
    layer=Image.new('RGBA',(n,n),(0,0,0,0));draw=ImageDraw.Draw(layer)
    at=lambda p:(ox+p[0]*scale,oy+p[1]*scale)
    for stroke in STROKES:
        # A dense run of discs gives a perfectly round stroke with no joint artefacts.
        points=[at(p) for p in stroke]
        if len(points)==2:points=[tuple(a+(b-a)*i/80 for a,b in zip(*points)) for i in range(81)]
        for x,y in points:draw.ellipse((x-width/2,y-width/2,x+width/2,y+width/2),fill=color)
    cx,cy=at((32,19.5));r=6*scale;draw.ellipse((cx-r,cy-r,cx+r,cy+r),fill=color)
    return layer

for size in (192,512):
    n=size*4
    image=Image.new('RGBA',(n,n),BG+(255,))
    glow=Image.new('RGBA',(n,n),(0,0,0,0));ImageDraw.Draw(glow).ellipse((n*.18,n*.18,n*.82,n*.82),fill=LAPIS+(110,))
    image.alpha_composite(glow.filter(ImageFilter.GaussianBlur(n*.12)))
    # Eye spans 58% of the width, centred, well inside the maskable safe zone.
    scale=n*.58/64;ox=(n-64*scale)/2;oy=(n-44*scale)/2
    halo=eye(n,scale,ox,oy,GOLD+(150,),3.2*scale).filter(ImageFilter.GaussianBlur(n*.02))
    image.alpha_composite(halo);image.alpha_composite(eye(n,scale,ox,oy,GOLD+(255,),3.2*scale))
    image.convert('RGB').resize((size,size),Image.Resampling.LANCZOS).save(root/'web'/f'icon-{size}x{size}.png')
print('Iconos IVOX generados: 192x192 y 512x512.')
