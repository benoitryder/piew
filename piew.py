#!/usr/bin/env python
# vim: fileencoding=utf-8

import gtk, gobject
import os
from math import ceil


class PiewApp:
  """Piew application.

  Instance attributes:
    w -- main window
    img -- image widget
    pb -- pixbuf object of the current image
    zoom -- current zoom
    pos_x,pos_y -- current image position (pixel displayed at windows's center)
    files -- list of browsed files
    cur_file -- displayed file
    _drag_x,_drag_y -- last drag position, or None
    _redraw_task -- ID of scheduled redraw task, or None
    _fullscreen -- window fullscreen state

  """

  # Configuration values

  w_min_size = (50,50)
  w_default_size = (800,500)
  default_files = ['.']
  bg_color = gtk.gdk.color_parse('black')
  move_step = 50
  # supported extensions (cas insensitive)
  file_exts = ('png','jpg','jpeg','gif','bmp')

  # List of zoom steps when zooming in/out
  zoom_steps = tuple(
      i/100.0 for i in
      range(  15,  50,   7) +
      range(  50, 100,  10) +
      range( 100, 200,  25) +
      range( 200, 600, 100) +
      range( 600,1000, 200) +
      range(1000,2000, 500) +
      range(2000,5000,1000)
      )

  # Interpolation type
  # Typical values are:
  #   gtk.gdk.INTERP_NEAREST   fast, low quality
  #   gtk.gdk.INTERP_BILINEAR  best quality/speed balance
  #interp_type = gtk.gdk.INTERP_NEAREST
  interp_type = gtk.gdk.INTERP_BILINEAR

  # Default (empty) pixbuf
  empty_pixbuf = gtk.gdk.Pixbuf(gtk.gdk.COLORSPACE_RGB,False,8,1,1)


  def __init__(self, files=None):
    self.cur_file = None
    if files is None or len(files) == 0:
      files = self.default_files
    self.set_filelist(files)

    self.w = gtk.Window(gtk.WINDOW_TOPLEVEL)
    self.w.set_title('Piew')
    self.w.set_default_size(*self.w_default_size)
    self.w.modify_bg(gtk.STATE_NORMAL, self.bg_color)

    self.img = gtk.Image()
    self.pb = self.empty_pixbuf
    self.img.set_from_pixbuf(self.pb)
    self.img.set_redraw_on_allocate(False)
    self.img.set_size_request(*self.w_min_size)

    self.w.add_events(gtk.gdk.BUTTON_PRESS_MASK|gtk.gdk.BUTTON_RELEASE_MASK|gtk.gdk.BUTTON1_MOTION_MASK)
    self.w.connect('destroy', self.quit)
    self.w.connect('size-allocate', self.event_resize)
    self.w.connect('key-press-event', self.event_kb_press)
    self.w.connect('scroll-event', self.event_mouse_scroll)
    self.w.connect('motion-notify-event', self.event_motion_notify)
    self.w.connect('button-press-event', self.event_button_press)
    self.w.connect('button-release-event', self.event_button_release)
    self.w.connect('window-state-event', self.event_window_state)

    self._redraw_task = None
    self._fullscreen = None
    self._drag_x, self._drag_y = None, None
    self.pos_x, self.pos_y = 0, 0
    self.zoom = 1

    self.w.add(self.img)
    self.w.show_all()
    self.change_file(0)


  def set_filelist(self, files):
    """Set list of image files.
    Directories are opened and images they contain are added.
    """
    self.files = []
    for f in files:
      if os.path.isfile(f):
        self.files.append(f)
      if os.path.isdir(f):
        for ff in sorted(os.listdir(f)):
          ff = os.path.join(f,ff)
          if os.path.isfile(ff):
            self.files.append(ff)
    self.files = filter(lambda f: f.split('.')[-1].lower() in self.file_exts, self.files)

  def change_file(self, n=0):
    """Change current file.
    n is the filelist move (eg. -1 for the previous file)
    or 0 to load the first file.
    """
    if n == 0 or self.cur_file is None:
      f = self.files[0]
    else:
      try:
        i = self.files.index(self.cur_file)
        f = self.files[ (i+n) % len(self.files) ]
      except ValueError:
        f = self.files[0]
    self.load_image(f)


  def load_image(self, fname):
    #TODO animate animated gifs
    self.pb = gtk.gdk.pixbuf_new_from_file(fname)
    self.cur_file = fname
    print "loaded '%s'" % fname #XXX:debug
    self.move()
    self.zoom_adjust()



  def refresh(self):
    """Schedule redrawing."""
    if self._redraw_task is not None:
      return
    self._redraw_task = gobject.idle_add(self.redraw)

  def redraw(self):
    """Redraw the image."""
    w_sx, w_sy = self.w.get_size()
    img_sx, img_sy = self.pb.get_width(), self.pb.get_height()
    pb = self.pb

    src_sx = int(ceil(w_sx/self.zoom))
    src_sy = int(ceil(w_sy/self.zoom))
    if src_sx < img_sx or src_sy < img_sy:
      pb = pb.subpixbuf(
          max(0,int((self.pos_x-w_sx/2)/self.zoom)),
          max(0,int((self.pos_y-w_sy/2)/self.zoom)),
          min(img_sx, src_sx), min(img_sy, src_sy)
          )

    #XXX display with NEAREST filter and schedule a 'nice' redraw
    if self.zoom != 1:
      dst_sx = int(ceil(self.zoom*pb.get_width()))
      dst_sy = int(ceil(self.zoom*pb.get_height()))
      pb = pb.scale_simple(
          min(w_sx, dst_sx), min(w_sy, dst_sy),
          self.interp_type
          )

    self.img.set_from_pixbuf(pb)
    # stop scheduled task
    self._redraw_task = None
    return False


  def move(self, pos=None, rel=True):
    """Move image display.
    pos are coordinates of the pixel displayed at window's center.
    If pos is None, image is centered.
    """

    w_sx, w_sy = self.w.get_size()
    img_sx, img_sy = int(self.pb.get_width()*self.zoom), int(self.pb.get_height()*self.zoom)
    if pos is None:
      x,y = img_sx/2,img_sy/2
    elif rel:
      x,y = pos[0]+self.pos_x , pos[1]+self.pos_y
    else:
      x,y = pos
    # clamp and center
    if img_sx <= w_sx: x = 0
    elif x < w_sx/2: x = w_sx/2
    else: x = min(x, img_sx - w_sx/2 - 1)
    if img_sy <= w_sy: y = 0
    elif y < w_sy/2: y = w_sy/2
    else: y = min(y, img_sy - w_sy/2 - 1)

    self.pos_x, self.pos_y = int(x), int(y)
    self.refresh()


  def set_zoom(self, z, center=None, rel=False):
    """Zoom at a given point.
    center is the pair of zoom center coordinates (in window pixels), relative
    to window's center
    """
    if rel:
      z += self.zoom
    assert 0.001 < z < 1000, "invalid zoom factor: %f" % z

    img_sx, img_sy = self.pb.get_width(), self.pb.get_height()
    w_sx, w_sy = self.w.get_size()

    if center is None:
      c_x, c_y = 0, 0
    else:
      c_x, c_y = center[0]-w_sx/2, center[1]-w_sy/2
    # Center
    # z (pos+c) = z' (pos'+c)
    # pos' = z'/z (pos+c) - c
    zf = z/self.zoom
    pos_x = zf * (self.pos_x+c_x) - c_x
    pos_y = zf * (self.pos_y+c_y) - c_y

    self.zoom = z
    self.move( (pos_x,pos_y), False)

  def zoom_in(self, center=None):
    for z in self.zoom_steps:
      if z > self.zoom:
        return self.set_zoom(z, center)
    return # do nothing

  def zoom_out(self, center=None):
    l = list(self.zoom_steps)
    l.reverse()
    for z in l:
      if z < self.zoom:
        return self.set_zoom(z, center)
    return # do nothing

  def zoom_adjust(self):
    """Set zoom to display the whole image."""
    w_sx, w_sy = self.w.get_size()
    img_sx, img_sy = self.pb.get_width(), self.pb.get_height()
    z = min( 1, float(w_sx)/img_sx, float(w_sy)/img_sy )
    self.set_zoom( z, None )

  def is_adjusted(self):
    """Return True if the whole image fits in the window."""
    w_sx, w_sy = self.w.get_size()
    return (
        w_sx >= int(self.pb.get_width()*self.zoom) and
        w_sy >= int(self.pb.get_height()*self.zoom)
        )


  def fullscreen(self, state=None):
    """Change fullscreen state
    True/False to set, None to toggle.
    """
    if state is None:
      state = not self._fullscreen
    if state:
      self.w.fullscreen()
    else:
      self.w.unfullscreen()


  def event_kb_press(self, w, ev):
    keyname = gtk.gdk.keyval_name(ev.keyval)
    if keyname in ('q','Escape'):
      self.quit()
    elif keyname == 'f':
      self.fullscreen()
    elif keyname in ('space','Page_Down'):
      self.change_file(+1)
    elif keyname in ('BackSpace','Page_Up'):
      self.change_file(-1)
    # Arrows
    elif keyname == 'Up':
      self.move( (0,-self.move_step) )
    elif keyname == 'Down':
      self.move( (0,+self.move_step) )
    elif keyname == 'Left':
      if self.is_adjusted():
        self.change_file(-1)
      else:
        self.move( (-self.move_step,0) )
    elif keyname == 'Right':
      if self.is_adjusted():
        self.change_file(+1)
      else:
        self.move( (+self.move_step,0) )
    # zoom
    elif keyname == 'plus':
      self.zoom_in()
    elif keyname == 'minus':
      self.zoom_out()
    else: # not processed
      print "key: %s" % keyname #XXX:debug
      return False
    return True


  def event_resize(self, w, alloc):
    pb = self.img.get_pixbuf()
    pb_sx, pb_sy = pb.get_width(), pb.get_height()
    if pb_sy < alloc.width or pb_sy < alloc.height:
      if pb_sx < self.pb.get_width()*self.zoom and pb_sy < self.pb.get_height()*self.zoom:
        self.refresh()
    return True

  def event_mouse_scroll(self, button, ev):
    if ev.direction == gtk.gdk.SCROLL_UP:
      self.zoom_in((ev.x,ev.y))
    elif ev.direction == gtk.gdk.SCROLL_DOWN:
      self.zoom_out((ev.x,ev.y))
    else:
      return
    return True

  def event_motion_notify(self, w, ev):
    if not ev.state|gtk.gdk.BUTTON1_MASK:
      return
    if self._drag_x is None:
      self._drag_x, self._drag_y = ev.x, ev.y
    self.move( (self._drag_x-ev.x, self._drag_y-ev.y) )
    self._drag_x, self._drag_y = ev.x, ev.y
    return True

  def event_button_press(self, w, ev):
    if ev.button == 1:
      self._drag_x, self._drag_y = None,None
      return True

  def event_button_release(self, w, ev):
    if self._drag_x is not None:
      return False
    if ev.button == 1:
      self.change_file(-1)
    elif ev.button == 3:
      self.change_file(+1)
    return True


  def event_window_state(self, w, ev):
    self._fullscreen = (ev.new_window_state & gtk.gdk.WINDOW_STATE_FULLSCREEN) != 0
    return True


  def main(self):
    gtk.main()

  def quit(self, *args):
    gtk.main_quit()


if __name__ == '__main__':
  import optparse
  parser = optparse.OptionParser(
      usage='usage: %prog [FILES]'
      )
  opts, args = parser.parse_args()

  app = PiewApp(args)
  app.main()

