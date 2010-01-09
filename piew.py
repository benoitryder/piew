#!/usr/bin/env python
# vim: fileencoding=utf-8

import gtk, gobject
import os, re


class PiewApp:
  """Piew application.

  Instance attributes:
    w -- main window
    img -- image widget
    info -- text information about displayed content
    pix_info -- text information about pixel
    cmd -- command line entry
    layout -- fixed widget which contains img and info
      The following extra attributes are set on layout:
        pos -- position of children (but img): {child:(x,y)}
    pb -- pixbuf object of the current image
    ani -- PixbufAnimation object (None for static images)
      The following extra attributes are set on ani:
        it -- PixbufAnimationIter object
        t -- current display time (always increases)
        _task -- ID of scheduled animation update, or None
    zoom -- current zoom
    pos_x,pos_y -- current image position (pixel displayed at windows's center)
    files -- list of browsed files
    _files_orig -- original list of files (used for refresh)
    cur_file -- displayed file, None (no file) or False (invalid file)
    _drag_x,_drag_y -- last drag position, or None
    _last_w_s -- last window size, used to detect effecting resizing
    _redraw_task -- ID of scheduled redraw task, or None
    _fullscreen -- window fullscreen state
    _mouse_x,_mouse_y -- current mouse position

  See configuration values, user events end commands for customization.

  """

  # Configuration values

  w_min_size = (50,50)
  w_default_size = (800,500)
  default_files = [u'.']
  bg_color = gtk.gdk.color_parse('black')

  # Format of info label, with Pango markup
  # The following characters are recognized:
  #   %f   image filename
  #   %w   image width (in pixels)
  #   %h   image height (in pixels)
  #   %z   zoom value (in %)
  #   %n   position of current image in file list
  #   %N   file list size
  #   %%   literal '%'
  info_format = '<span font_desc="Sans 10" color="green">%f  ( %w x %h )  [ %n / %N ]  %z %%</span>'
  # Info label position (offset from top left corner)
  # Negative positions are relative to the opposite side.
  info_position = (10,5)
  # Filename substitutes for invalid files (Pango markup)
  info_txt_no_image = '<i>no file</i>'
  info_txt_bad_image = '<i>invalid file format</i>'

  # Format of information about pixel under the cursor
  # If cursor is not on the image, an empty string is returned.
  #   %r,%g,%b,%a  color values (alpha displayed only if available)
  #   %a           value of alpha channel, if available
  #   %h           color value, HTML format (lowercase), no leading '#')
  #   %H           same as %h but uppercase
  #   %i,%I        same as %h and %H but without alpha channel
  #   %x,%y        pixel position
  pix_info_format = '<span color="magenta">( %x , %y ) <tt> <span background="#%I">  </span> #%H  <span color="red">%r</span> <span color="green">%g</span> <span color="blue">%b</span> <span color="white">%a</span></tt></span>'
  # Pixel Info label position (offset from top left corner)
  # Negative positions are relative to the opposite side.
  pix_info_position = (10,30)

  # Command line position
  # Negative positions are relative to the opposite side.
  cmd_position = (0,-1)

  # Step (in pixels) when moving around with arrow keys
  # Keys are GDK Modifier masks (None for default value).
  move_step = {
      None: 50,
      gtk.gdk.MOD1_MASK: 10,
      gtk.gdk.SHIFT_MASK: 500,
      }
  # Step when moving through filelist.
  filelist_step = {
      None: 1,
      gtk.gdk.SHIFT_MASK: 5,
      }

  # supported extensions (cas insensitive)
  file_exts = reduce(lambda l,f:l+f['extensions'], gtk.gdk.pixbuf_get_formats(), [] )

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

  # Frame duration of infinite frames (in ms)
  # Animation could stop at the last frame (without looping).
  # This value provides a finite display time for such frames.
  ani_infinite_frame_duration = 2000

  # Empty pixbuf (or image) for invalid files
  empty_pixbuf = gtk.gdk.Pixbuf(gtk.gdk.COLORSPACE_RGB,False,8,1,1)
  empty_pixbuf.fill(0)


  # Application birth and death methods

  def __init__(self, files=None):
    self.cur_file = None
    if files is None or len(files) == 0:
      files = self.default_files
    self.set_filelist(files)

    self.w = gtk.Window(gtk.WINDOW_TOPLEVEL)
    self.w.set_title('Piew')
    self.w.set_default_size(*self.w_default_size)
    self.w.modify_bg(gtk.STATE_NORMAL, self.bg_color)


    # Layout and its elements
    self.layout = gtk.Fixed()

    self.info = gtk.Label()
    self.info.set_use_markup(True)
    self.info.set_use_underline(False)
    self.info.set_markup('-')
    self.pix_info = gtk.Label()
    self.pix_info.set_use_markup(True)
    self.pix_info.set_use_underline(False)
    self.pix_info.set_markup('-')

    self.cmd = gtk.Entry()
    self.cmd.set_no_show_all(True)
    self.cmd.connect('activate', self.event_cmd_activate)

    self.img = gtk.Image()
    self.pb = self.empty_pixbuf
    self.ani = None
    self.img.set_from_pixbuf(self.pb)
    self.img.set_redraw_on_allocate(False)

    self.layout.put(self.img, 0, 0)
    self.layout.pos = {
        self.info: self.info_position,
        self.pix_info: self.pix_info_position,
        self.cmd: self.cmd_position,
        }
    for w,pos in self.layout.pos.items():
      self.layout.put(w, *pos)
    self.layout.set_size_request(*self.w_min_size)


    self.w.add_events(gtk.gdk.BUTTON_PRESS_MASK|gtk.gdk.BUTTON_RELEASE_MASK|gtk.gdk.POINTER_MOTION_MASK)
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
    self._mouse_x, self._mouse_y = 0, 0
    self._drag_x, self._drag_y = None, None
    self._last_w_s = (0,0) # force resize event to occur at startup
    self.pos_x, self.pos_y = 0, 0
    self.zoom = 1

    self.w.add(self.layout)
    self.w.show_all()
    self.change_file(0,False)

  def main(self):
    gtk.main()

  def quit(self, *args):
    gtk.main_quit()


  # File related methods

  def set_filelist(self, files=None):
    """Set or reload list of image files.
    Directories are opened and images they contain are added.
    If files is None, the original filelist is reloaded.
    Doublets are removed, files are sorted (string comparaison).
    """
    if files is not None:
      self._files_orig = files
    self.files = set() # not doublets
    for f in self._files_orig:
      f = unicode( os.path.normpath( unicode(f) ) )
      if os.path.isfile(f):
        self.files.add(f)
      if os.path.isdir(f):
        for ff in sorted(os.listdir(f)):
          ff = os.path.join(f,ff)
          if os.path.isfile(ff):
            self.files.add(ff)
    # convert to a list, filter, sort
    self.files = filter(lambda f: f.split('.')[-1].lower() in self.file_exts, list(self.files))
    self.files.sort()

  def change_file(self, n=0, rel=True):
    """Change current file.
    n is the filelist position, relative to current position if rel is True.
    Absolute and relative positions wrap around the bounds of the list.
    On error the first file is loaded.
    """
    if len(self.files) == 0:
      f = None
    elif self.cur_file is None:
      f = self.files[0]
    else:
      try:
        if rel:
          n += self.files.index(self.cur_file)
        f = self.files[ n % len(self.files) ]
      except ValueError:
        f = self.files[0]
    self.load_image(f)
    self.zoom_adjust()

  def load_image(self, fname):
    """Load a given image.
    If fname is None, display will be cleared and info text will be properly
    set.
    """
    self.ani_set_state(False)
    self.ani = None
    self.pb = None
    if fname is None:
      self.pb = self.empty_pixbuf
    else:
      try:
        ani = gtk.gdk.PixbufAnimation(fname)
      except gobject.GError, e: # invalid format
        print "Invalid image '%s': %s" % (fname, e)
        ani = None
      if ani is not None:
        if ani.is_static_image():
          self.ani = None
          self.pb = ani.get_static_image()
        else:
          ani.t = 1  # 0.0 is a special value, don't use it
          ani.it = ani.get_iter(ani.t)
          ani._task = None
          self.ani = ani
          self.pb = self.ani.it.get_pixbuf()
          self.ani_update()  # start animation
      else:
        self.pb = self.empty_pixbuf
        fname = False
    self.cur_file = fname
    self.move()

  def ani_update(self):
    """Advance animation.
    If a task has been defined, animation is advanced to the next frame.
    Schedule next update.
    Always returns False (to be used as gobject event callback).
    """
    if self.ani._task is not None:
      self.ani_next_frame()
    t = self.ani.it.get_delay_time()
    if t == -1:
      t = self.ani_infinite_frame_duration
    self.ani._task = gobject.timeout_add(t, self.ani_update)
    return False


  # Drawing methods

  def refresh(self):
    """Schedule redrawing."""
    if self._redraw_task is not None:
      return
    self._redraw_task = gobject.idle_add(self.redraw)

  def redraw(self):
    """Redraw the image.
    Always returns False (to be used as gobject event callback).
    """
    w_sx, w_sy = self.w.get_size()
    img_sx, img_sy = self.pb.get_width(), self.pb.get_height()
    pb = self.pb

    src_sx, src_sy = w_sx/self.zoom, w_sy/self.zoom
    if src_sx < img_sx or src_sy < img_sy:
      src_x = max(0,int(self.pos_x-src_sx/2))
      src_y = max(0,int(self.pos_y-src_sy/2))
      pb = pb.subpixbuf(
          src_x, src_y,
          int(min(src_sx, img_sx-src_x)),
          int(min(src_sy, img_sy-src_y))
          )

    #XXX display with NEAREST filter and schedule a 'nice' redraw
    if self.zoom != 1:
      dst_sx = int(self.zoom*pb.get_width())
      dst_sy = int(self.zoom*pb.get_height())
      pb = pb.scale_simple(
          min(w_sx, dst_sx), min(w_sy, dst_sy),
          self.interp_type
          )

    self.img.set_from_pixbuf(pb)

    # Center image
    self.layout.move(self.img, (w_sx-pb.get_width())/2, (w_sy-pb.get_height())/2)

    self.redraw_info()

    # stop scheduled task
    self._redraw_task = None
    return False

  def redraw_info(self):
    """Redraw image info."""
    self.info.set_markup( self.format_info() )

  def format_info(self):
    """Return Pango markup for self.info."""
    # Get formatting data
    d = {
        'w': self.pb.get_width(),
        'h': self.pb.get_height(),
        'z': int(self.zoom * 100),
        'N': len(self.files),
        '%': '%',
        }
    # Filename
    if self.cur_file is None:
      d['f'] = self.info_txt_no_image
    elif self.cur_file is False:
      d['f'] = self.info_txt_bad_image
    else:
      d['f'] = gobject.markup_escape_text( self.cur_file )
    # File position
    try:
      d['n'] = self.files.index(self.cur_file) + 1
    except ValueError:
      d['n'] = '?'

    # Format
    return re.sub(
        '%(['+''.join(d.keys())+'])',
        lambda m: str( d[ m.group(1) ] ),
        self.info_format
        )

  def redraw_pix_info(self, pos=None):
    """Redraw pixel info."""
    s = self.format_pix_info(pos)
    if s is None:
      self.pix_info.hide()
    else:
      self.pix_info.set_markup( s )
      self.pix_info.show()

  def format_pix_info(self, pos=None):
    """Return Pango markup for pixel info."""
    if pos is None:
      pos = self.get_cursor_pixel()
      if pos is None:
        return None
    else:
      img_sx, img_sy = self.pb.get_width(), self.pb.get_height()
      if not (0 <= pos[0] < img_sx and 0 <= pos[1] < img_sy):
        return None
    colors = self.get_pixel_color(*pos)
    if len(colors) < 3:
      return '' # should not happen with normal images
    # Get formatting data
    d = {
        'x': pos[0], 'y': pos[1],
        'r': colors[0],
        'g': colors[1],
        'b': colors[2],
        'a': '' if len(colors)<4 else colors[3],
        'h': ''.join( '%02x'%c for c in colors ),
        'H': ''.join( '%02X'%c for c in colors ),
        'i': ''.join( '%02x'%c for c in colors[:3] ),
        'I': ''.join( '%02X'%c for c in colors[:3] ),
        '%': '%',
        }
    # Format
    return re.sub(
        '%(['+''.join(d.keys())+'])',
        lambda m: str( d[ m.group(1) ] ),
        self.pix_info_format
        )


  # Image position, zoom, etc.

  def move(self, pos=None, rel=True):
    """Move image display.
    pos are coordinates of the pixel displayed at window's center.
    If pos is None, image is centered.
    """

    w_sx, w_sy = self.w.get_size()
    img_sx, img_sy = self.pb.get_width(), self.pb.get_height()
    if pos is None:
      x,y = img_sx/2,img_sy/2
    elif rel:
      x,y = pos[0]+self.pos_x , pos[1]+self.pos_y
    else:
      x,y = pos
    # clamp and center
    dst_sx, dst_sy = float(w_sx)/self.zoom, float(w_sy)/self.zoom
    if img_sx <= dst_sx: x = img_sx/2
    elif x < dst_sx/2: x = dst_sx/2
    else: x = min(x, img_sx - dst_sx/2 - 1)
    if img_sy <= dst_sy: y = img_sy/2
    elif y < dst_sy/2: y = dst_sy/2
    else: y = min(y, img_sy - dst_sy/2 - 1)

    self.pos_x, self.pos_y = x, y
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
    # pos+c/z = pos'+c/z'
    # pos' = pos + c*(1/z-1/z')
    zk = 1./self.zoom - 1./z
    pos_x = self.pos_x + c_x * zk
    pos_y = self.pos_y + c_y * zk

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

  def is_adjusted(self):
    """Return True if the whole image fits in the window."""
    w_sx, w_sy = self.w.get_size()
    return (
        w_sx >= int(self.pb.get_width()*self.zoom) and
        w_sy >= int(self.pb.get_height()*self.zoom)
        )

  def ani_is_playing(self):
    if self.ani is None:
      return False
    return self.ani._task is not None

  def ani_set_state(self, state=None):
    """Set animation play state.

    state values:
      None -- toggle play/pause
      True -- play animation
      False -- pause animation

    Current state is defined by self.ani._task.

    """
    if self.ani is None:
      return  # silently ignore static images
    cur_state = self.ani_is_playing()
    if state == cur_state:
      return
    # Toggle state
    if cur_state:
      gobject.source_remove(self.ani._task)
      self.ani._task = None
    else:
      self.ani_update()

  def ani_next_frame(self):
    if self.ani is None:
      return  # silently ignore static images
    self.ani.t += self.ani.it.get_delay_time()/1000.
    if self.ani.it.advance(self.ani.t):
      self.pb = self.ani.it.get_pixbuf()
      self.redraw()

  def get_pixel_color(self, x, y):
    """Get color of a given pixel.
    Return a tuple with self.pb.get_n_channels() values.
    """
    # Get a pixbuf with a single pixel
    # This avoid to retrieve the whole image data with get_pixels()
    pb = self.pb.subpixbuf(x,y,1,1)
    n = pb.get_n_channels()
    return tuple( ord(c) for c in pb.get_pixels()[0:n] )

  def get_cursor_pixel(self):
    """Get position of pixel under the cursor.
    Position is returned as a (x,y) pair.
    Return None if cursor is not on the image.
    """
    img_sx, img_sy = self.pb.get_width(), self.pb.get_height()
    w_sx, w_sy = self.w.get_size()
    x = int(round( float(self._mouse_x-w_sx/2)/self.zoom + self.pos_x ))
    y = int(round( float(self._mouse_y-w_sy/2)/self.zoom + self.pos_y ))
    if 0 <= x < img_sx and 0 <= y < img_sy:
      return (x,y)
    return None


  # Internal events

  def event_resize(self, w, alloc):
    if self._last_w_s != self.w.get_size():
      self._last_w_s = self.w.get_size()
      # repositionate layout elements
      for w,pos in self.layout.pos.items():
        if pos[0] >= 0 and pos[1] >= 0: continue
        x,y = pos
        if x < 0:
          x += self._last_w_s[0] - w.get_allocation().width
        if y < 0:
          y += self._last_w_s[1] - w.get_allocation().height
        self.layout.move(w, x, y)
      self.refresh()
    return True

  def event_window_state(self, w, ev):
    self._fullscreen = (ev.new_window_state & gtk.gdk.WINDOW_STATE_FULLSCREEN) != 0
    return True


  # User events (tweak them to your liking)
  # New events may be defined in __init__() (see w.connect() calls).
  # (And don't forget to update flags in w.add_events() call if needed!)

  def event_kb_press(self, w, ev):
    keyname = gtk.gdk.keyval_name(ev.keyval)
    if self.cmd.is_focus():
      if keyname == 'Escape':
        self.cmd.hide()
      return False

    if keyname in ('q','Escape'):
      self.quit()
    elif keyname == 'f':
      self.fullscreen()
    elif keyname in ('space','Page_Down'):
      self.change_file(+self.get_filelist_step(ev))
    elif keyname in ('BackSpace','Page_Up'):
      self.change_file(-self.get_filelist_step(ev))
    # arrows
    elif keyname == 'Up':
      self.move( (0,-self.get_move_step(ev)) )
    elif keyname == 'Down':
      self.move( (0,+self.get_move_step(ev)) )
    elif keyname == 'Left':
      if self.is_adjusted():
        self.change_file(-self.get_filelist_step(ev))
      else:
        self.move( (-self.get_move_step(ev),0) )
    elif keyname == 'Right':
      if self.is_adjusted():
        self.change_file(+self.get_filelist_step(ev))
      else:
        self.move( (+self.get_move_step(ev),0) )
    # zoom
    elif keyname == 'plus':
      self.zoom_in()
    elif keyname == 'minus':
      self.zoom_out()
    elif keyname == 'a':
      self.zoom_adjust()
    elif keyname == 'z':
      self.set_zoom(1)
    # reload current image
    elif keyname == 'r':
      self.load_image(self.cur_file)
    # refresh file list
    elif keyname == 'F5':
      self.set_filelist()
    # animation
    elif keyname == 'p':
      self.ani_set_state()
    elif keyname == 'n':
      self.ani_next_frame()
    # commands
    elif keyname == 'colon':
      self.cmd_show()
    elif keyname == 'g':
      self.cmd_show('goto ')

    # delete file (ask for confirmation)
    elif keyname == 'Delete' and self.cur_file:
      dlg = gtk.MessageDialog(self.w, gtk.DIALOG_MODAL,
          gtk.MESSAGE_QUESTION, gtk.BUTTONS_OK_CANCEL
          )
      dlg.set_title("Piew, delete file")
      dlg.set_markup("Deleting '%s'.\nAre your sure?" % gobject.markup_escape_text(self.cur_file))
      ret = dlg.run()
      dlg.destroy()
      if ret == gtk.RESPONSE_OK:
        # remove file
        del_f = self.cur_file
        try:
          os.remove(del_f)
        except OSError, e:
          print "Cannot delete '%s': %s" % (self.cur_file, e)
          return True
        # update filelist and current image
        if len(self.files) == 1:
          # this image was the last one
          self.files = []
          self.load_image(None)
        else:
          self.change_file(+1)
          try: # just in case, it's safer
            self.files.remove(del_f)
          except:
            pass

    else: # not processed
      return False
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
    self._mouse_x, self._mouse_y = ev.x, ev.y
    if ev.state&gtk.gdk.CONTROL_MASK:
      self.redraw_pix_info()
    if not ev.state&gtk.gdk.BUTTON1_MASK:
      return
    if self._drag_x is None:
      self._drag_x, self._drag_y = ev.x, ev.y
    self.move((
      (self._drag_x-ev.x)/self.zoom,
      (self._drag_y-ev.y)/self.zoom
      ))
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
      if ev.state&gtk.gdk.CONTROL_MASK:
        self.redraw_pix_info()
        return True
      self.change_file(-1)
    elif ev.button == 3:
      self.change_file(+1)
    return True

  # Helper methods to get step from event
  def get_move_step(self, ev):
    try:
      return self.move_step[ev.state]
    except KeyError:
      return self.move_step[None]
  def get_filelist_step(self, ev):
    try:
      return self.filelist_step[ev.state]
    except KeyError:
      return self.filelist_step[None]


  # Command line

  def cmd_show(self, txt=''):
    """Display command entry with given text."""
    self.cmd.set_text(txt)
    self.cmd.show()
    # when hidding, cmd has no height, force resize
    self._last_w_s = (0,0)
    self.cmd.grab_focus()
    self.cmd.set_position(-1)

  def event_cmd_activate(self, w):
    args = self.cmd.get_text().split(None,1)
    if len(args) > 0:
      if len(args) == 1:
        args.append('')
      try:
        { # cmd_name: cmd_function
            'eval': self.cmd_eval,
            'goto': self.cmd_goto,
            'pixel': self.cmd_pixel,
        }[args[0]](args[1])
      except Exception, e:
        print "command error: %s" % e
    w.hide()
    return False

  def cmd_eval(self, s):
    eval(s, globals(), {'self':self})

  def cmd_goto(self, s):
    """Go to a given image, by index."""
    if s[0] in "+-":
      self.change_file(int(s), True)
    else:
      self.change_file(int(s)-1, False)

  def cmd_pixel(self, s):
    self.redraw_pix_info( map(int, s.split()) )



if __name__ == '__main__':
  import optparse
  parser = optparse.OptionParser(
      usage='usage: %prog [FILES]'
      )
  opts, args = parser.parse_args()

  app = PiewApp(args)
  app.main()

