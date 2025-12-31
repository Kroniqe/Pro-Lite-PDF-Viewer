import sys
import traceback 

# 1. SETUP CRASH HANDLER
def crash_handler(etype, value, tb):
    error_msg = "".join(traceback.format_exception(etype, value, tb))
    print(error_msg)
    try:
        from PyQt6.QtWidgets import QMessageBox, QApplication
        if QApplication.instance():
            QMessageBox.critical(None, "Critical Error", f"The application crashed:\n\n{error_msg}")
        else:
            with open("crash_log.txt", "w") as f:
                f.write(error_msg)
    except:
        with open("crash_log.txt", "w") as f:
            f.write(error_msg)
    sys.exit(1)

sys.excepthook = crash_handler

# 2. IMPORTS
try:
    import fitz  # PyMuPDF library
    import os
    import math 
    
    try:
        import requests
        HAS_REQUESTS = True
    except ImportError:
        HAS_REQUESTS = False

    from PyQt6.QtWidgets import (QApplication, QMainWindow, QGraphicsView, QGraphicsScene, 
                                 QToolBar, QFileDialog, QMessageBox, QComboBox, 
                                 QLabel, QGraphicsPixmapItem, QRubberBand, QWidget, QMenu,
                                 QInputDialog, QLineEdit, QGraphicsPathItem, QTabWidget,
                                 QVBoxLayout) 
    from PyQt6.QtGui import (QImage, QPixmap, QPainter, QAction, QColor, QKeySequence, 
                             QShortcut, QIcon, QFont, QDesktopServices, QCursor, QIntValidator,
                             QPainterPath, QPen, QPolygonF)
    from PyQt6.QtCore import Qt, QRect, QPoint, QSize, QEvent, QUrl, QPointF, QRectF, pyqtSignal

except Exception as e:
    with open("startup_error.txt", "w") as f:
        f.write(str(e))
    raise e

# --- PAGE ITEM WRAPPER ---
class PDFPageItem(QGraphicsPixmapItem):
    """ A wrapper to handle a single PDF page in the scene """
    def __init__(self, page_obj, page_num):
        super().__init__()
        self.page_obj = page_obj
        self.page_num = page_num
        self.setAcceptHoverEvents(True)
        self.setTransformationMode(Qt.TransformationMode.SmoothTransformation)

    def update_render(self, scale, dpr):
        try:
            mat = fitz.Matrix(scale * dpr, scale * dpr)
            pix = self.page_obj.get_pixmap(matrix=mat)
            
            # CRITICAL FIX: Check if samples exist (prevents crash on empty/corrupt pages)
            if not pix.samples:
                return

            if pix.alpha:
                fmt = QImage.Format.Format_RGBA8888
            else:
                fmt = QImage.Format.Format_RGB888

            qimg = QImage(pix.samples, pix.width, pix.height, pix.stride, fmt)
            qimg = qimg.copy() # Deep copy to prevent memory errors
            qimg.setDevicePixelRatio(dpr)
            self.setPixmap(QPixmap.fromImage(qimg))
        except Exception as e:
            print(f"Render Error on page {self.page_num}: {e}")


# --- INDIVIDUAL TAB WIDGET ---
class PDFTab(QWidget):
    """ Holds the logic for ONE open document """
    
    # Signals to update the Main Window UI when this tab changes
    zoom_changed = pyqtSignal(float)
    page_changed = pyqtSignal(str) # "current / total"
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # State
        self.doc = None
        self.current_path = None
        self.scale = 1.0
        self.view_mode = "single"
        self.tool_mode = "browse"
        self.highlight_color = (1, 1, 0)
        
        self.page_items = []
        
        # Selection State
        self.start_selection_page = None 
        self.page_quads_cache = [] 
        self.sel_start_index = -1
        self.sel_end_index = -1
        self.selected_quads = [] 
        self.use_linear_selection = False
        
        # Layout
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0,0,0,0)
        
        self.scene = QGraphicsScene()
        self.scene.setBackgroundBrush(QColor("#505050"))
        
        self.view = QGraphicsView(self.scene)
        self.view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.view.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.view.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.SmartViewportUpdate)
        
        self.layout.addWidget(self.view)
        
        # Events
        self.view.verticalScrollBar().valueChanged.connect(self.check_current_page)
        self.view.viewport().installEventFilter(self)
        
        # Tools
        self.rubberband = QRubberBand(QRubberBand.Shape.Rectangle, self.view)
        self.origin_point = QPoint()
        
        self.selection_overlay = QGraphicsPathItem()
        self.selection_overlay.setBrush(QColor(0, 50, 200, 60)) 
        self.selection_overlay.setPen(QPen(Qt.GlobalColor.transparent))
        self.selection_overlay.setZValue(10)
        self.scene.addItem(self.selection_overlay)

    def load_document(self, doc, path=None):
        self.doc = doc
        self.current_path = path
        self.render_pages()
        self.check_current_page()

    def render_pages(self):
        if not self.doc: return
        self.scene.clear()
        self.page_items.clear()
        
        # Re-add selection overlay
        self.selection_overlay = QGraphicsPathItem()
        self.selection_overlay.setBrush(QColor(0, 50, 200, 60)) 
        self.selection_overlay.setPen(QPen(Qt.GlobalColor.transparent))
        self.selection_overlay.setZValue(10)
        self.scene.addItem(self.selection_overlay)

        dpr = self.devicePixelRatio() 
        x_offset, y_offset, padding = 0, 0, 20
        
        for i, page in enumerate(self.doc):
            item = PDFPageItem(page, i)
            item.update_render(self.scale, dpr)
            self.scene.addItem(item)
            self.page_items.append(item)
            item_w, item_h = item.boundingRect().width(), item.boundingRect().height()

            if self.view_mode == "single":
                view_w = self.view.width() if self.view.width() > 0 else 800
                pos_x = (view_w - item_w) / 2 if item_w < view_w else 0
                item.setPos(pos_x, y_offset)
                y_offset += item_h + padding
            elif self.view_mode == "dual":
                if i % 2 == 0:
                    item.setPos(x_offset, y_offset)
                    next_x = x_offset + item_w + padding
                else:
                    item.setPos(next_x, y_offset)
                    prev_h = self.page_items[i-1].boundingRect().height()
                    y_offset += max(item_h, prev_h) + padding

        self.scene.setSceneRect(self.scene.itemsBoundingRect())
        self.zoom_changed.emit(self.scale)

    def zoom_view(self, change):
        self.scale += change
        if self.scale < 0.2: self.scale = 0.2
        if self.scale > 5.0: self.scale = 5.0
        self.render_pages()

    def fit_width(self):
        if not self.doc: return
        view_w = self.view.width() - 30
        page_w_points = self.doc[0].rect.width
        if page_w_points > 0:
            self.scale = view_w / page_w_points
            self.render_pages()

    def check_current_page(self):
        if not self.page_items: return
        try:
            # SAFETY CHECK: Ensure scene exists and items are valid
            if not self.scene or not self.view: return
            
            center_y = self.view.mapToScene(0, self.view.height()//2).y()
            for item in self.page_items:
                try:
                    # Check if C++ object deleted
                    if item.scene() is None: continue 
                    rect = item.sceneBoundingRect()
                    if rect.top() <= center_y <= rect.bottom():
                        # Signal the main window to update text
                        self.page_changed.emit(f"{item.page_num + 1} / {len(self.doc)}")
                        return
                except RuntimeError:
                    continue 
        except:
            pass
            
    def jump_to_page(self, page_num_1_based):
        if not self.page_items: return
        try:
            idx = page_num_1_based - 1
            if 0 <= idx < len(self.page_items):
                item = self.page_items[idx]
                self.view.centerOn(item)
        except:
            pass

    def set_tool(self, mode):
        self.tool_mode = mode
        if mode == "browse":
            self.view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            self.selection_overlay.setPath(QPainterPath()) 
            self.selected_quads = []
            self.rubberband.hide()
            self.view.setCursor(Qt.CursorShape.ArrowCursor)
        elif mode == "erase":
            self.view.setDragMode(QGraphicsView.DragMode.NoDrag)
            self.view.setCursor(Qt.CursorShape.CrossCursor)
            self.rubberband.hide()
        else:
            self.view.setDragMode(QGraphicsView.DragMode.NoDrag)
            self.view.setCursor(Qt.CursorShape.IBeamCursor)
            
    def eventFilter(self, source, event):
        if source == self.view.viewport() and self.tool_mode != "browse" and self.doc:
            if event.type() == QEvent.Type.MouseButtonPress:
                if self.tool_mode == "erase":
                    self.process_eraser(event.pos())
                    return True
                else:
                    self.handle_selection_press(event)
                    return True
            elif event.type() == QEvent.Type.MouseMove:
                if self.tool_mode != "erase":
                    self.handle_selection_move(event)
                    return True
            elif event.type() == QEvent.Type.MouseButtonRelease:
                if self.tool_mode != "erase":
                    self.handle_selection_release(event)
                    return True
        return super().eventFilter(source, event)

    # --- SELECTION LOGIC ---
    def handle_selection_press(self, event):
        self.origin_point = event.pos()
        self.selection_overlay.setPath(QPainterPath()) 
        self.selected_quads = []
        self.page_quads_cache = [] 
        self.use_linear_selection = False 
        
        scene_pos = self.view.mapToScene(self.origin_point)
        for item in self.page_items:
            try:
                if item.scene() is None: continue
                if item.sceneBoundingRect().contains(scene_pos):
                    self.start_selection_page = item
                    try:
                        self.page_quads_cache = item.page_obj.get_text("quads")
                        if self.page_quads_cache:
                            self.use_linear_selection = True
                            local_pos = item.mapFromScene(scene_pos)
                            self.sel_start_index = self.find_closest_quad_index(local_pos.x()/self.scale, local_pos.y()/self.scale)
                    except:
                        self.use_linear_selection = False
                    
                    if not self.use_linear_selection:
                        self.rubberband.setGeometry(QRect(self.origin_point, QSize()))
                        self.rubberband.show()
                    break
            except RuntimeError: continue

    def handle_selection_move(self, event):
        if not self.origin_point.isNull() and self.start_selection_page:
            if self.use_linear_selection and self.page_quads_cache:
                scene_pos = self.view.mapToScene(event.pos())
                try:
                    local_pos = self.start_selection_page.mapFromScene(scene_pos)
                    self.sel_end_index = self.find_closest_quad_index(local_pos.x()/self.scale, local_pos.y()/self.scale)
                    
                    # CRITICAL FIX: Ensure indices are valid (not -1) before slicing
                    if self.sel_start_index != -1 and self.sel_end_index != -1:
                        i1 = min(self.sel_start_index, self.sel_end_index)
                        i2 = max(self.sel_start_index, self.sel_end_index)
                        self.selected_quads = self.page_quads_cache[i1 : i2 + 1]
                        self.draw_selection_visuals()
                except RuntimeError: self.start_selection_page = None 
            else:
                self.rubberband.setGeometry(QRect(self.origin_point, event.pos()).normalized())

    def handle_selection_release(self, event):
        if self.tool_mode == "highlight":
            if self.use_linear_selection and self.selected_quads:
                self.highlight_selection(self.highlight_color)
            else:
                rect = self.rubberband.geometry()
                if rect.width() > 5: self.process_box_highlight(rect)
        else: # Select
            if self.use_linear_selection and self.selected_quads:
                self.show_context_menu(event.globalPosition().toPoint())
            else:
                rect = self.rubberband.geometry()
                if rect.width() > 5: self.process_box_menu(rect, event.globalPosition().toPoint())
        
        self.rubberband.hide()
        self.origin_point = QPoint()
        self.start_selection_page = None

    def process_eraser(self, screen_pos):
        scene_pos = self.view.mapToScene(screen_pos)
        for item in self.page_items:
            try:
                if item.scene() is None: continue
                if item.sceneBoundingRect().contains(scene_pos):
                    local_pos = item.mapFromScene(scene_pos)
                    point = fitz.Point(local_pos.x()/self.scale, local_pos.y()/self.scale)
                    annot = item.page_obj.first_annot
                    while annot:
                        if annot.rect.contains(point) and annot.type[0] == 8: 
                            item.page_obj.delete_annot(annot)
                            item.update_render(self.scale, self.devicePixelRatio())
                            return 
                        annot = annot.next
                    break
            except: pass

    def find_closest_quad_index(self, x, y):
        if not self.page_quads_cache: return -1
        best_dist = float('inf')
        best_idx = -1
        for i, q in enumerate(self.page_quads_cache):
            if q.rect.contains(fitz.Point(x,y)): return i
            cx, cy = (q.ul.x + q.lr.x)/2, (q.ul.y + q.lr.y)/2
            dist = (cx - x)**2 + (cy - y)**2
            if dist < best_dist: best_dist, best_idx = dist, i
        return best_idx

    def draw_selection_visuals(self):
        path = QPainterPath()
        try:
            for q in self.selected_quads:
                poly = QPolygonF([QPointF(q.ul.x*self.scale, q.ul.y*self.scale),
                                  QPointF(q.ur.x*self.scale, q.ur.y*self.scale),
                                  QPointF(q.lr.x*self.scale, q.lr.y*self.scale),
                                  QPointF(q.ll.x*self.scale, q.ll.y*self.scale)])
                poly = self.start_selection_page.mapToScene(poly)
                path.addPolygon(poly)
            self.selection_overlay.setPath(path)
        except: pass

    def process_box_highlight(self, rect_screen):
        if not self.start_selection_page: return
        try:
            rect_scene = self.view.mapToScene(rect_screen).boundingRect()
            intersection = rect_scene.intersected(self.start_selection_page.sceneBoundingRect())
            local = intersection.translated(-self.start_selection_page.pos())
            pdf_rect = fitz.Rect(local.x()/self.scale, local.y()/self.scale, local.right()/self.scale, local.bottom()/self.scale)
            try: quads = self.start_selection_page.page_obj.get_text("quads", clip=pdf_rect)
            except: quads = None
            annot = self.start_selection_page.page_obj.add_highlight_annot(quads if quads else pdf_rect)
            annot.set_colors(stroke=self.highlight_color)
            annot.update()
            self.start_selection_page.update_render(self.scale, self.devicePixelRatio())
        except: pass

    def process_box_menu(self, rect_screen, global_pos):
        if not self.start_selection_page: return
        try:
            rect_scene = self.view.mapToScene(rect_screen).boundingRect()
            intersection = rect_scene.intersected(self.start_selection_page.sceneBoundingRect())
            local = intersection.translated(-self.start_selection_page.pos())
            pdf_rect = fitz.Rect(local.x()/self.scale, local.y()/self.scale, local.right()/self.scale, local.bottom()/self.scale)
            self.selected_quads = [pdf_rect]
            try: 
                real = self.start_selection_page.page_obj.get_text("quads", clip=pdf_rect)
                if real: self.selected_quads = real
            except: pass
            self.show_context_menu(global_pos)
        except: pass

    def show_context_menu(self, global_pos):
        menu = QMenu(self)
        menu.addAction("Copy Text").triggered.connect(self.copy_selection)
        menu.addSeparator()
        colors = [("Yellow", (1, 1, 0)), ("Green", (0, 1, 0)), ("Red", (1, 0, 0)), ("Blue", (0, 0, 1))]
        for name, rgb in colors:
            menu.addAction(f"Highlight {name}").triggered.connect(lambda c, rgb=rgb: self.highlight_selection(rgb))
        menu.exec(global_pos)

    def copy_selection(self):
        if not self.selected_quads or not self.start_selection_page: return
        full_text = ""
        try:
            if isinstance(self.selected_quads[0], fitz.Rect):
                 full_text = self.start_selection_page.page_obj.get_text("text", clip=self.selected_quads[0])
            else:
                for q in self.selected_quads:
                    try: full_text += self.start_selection_page.page_obj.get_text("text", clip=q.rect)
                    except: pass
            QApplication.clipboard().setText(full_text.strip())
            self.selection_overlay.setPath(QPainterPath()) 
        except: pass

    def highlight_selection(self, color_rgb):
        if not self.selected_quads or not self.start_selection_page: return
        try:
            annot = self.start_selection_page.page_obj.add_highlight_annot(self.selected_quads)
            annot.set_colors(stroke=color_rgb)
            annot.update()
            self.start_selection_page.update_render(self.scale, self.devicePixelRatio())
            self.selection_overlay.setPath(QPainterPath())
            self.selected_quads = []
        except: pass


# --- MAIN WINDOW (MANAGES TABS) ---
class ProPDFViewer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Pro-Lite PDF Viewer")
        self.resize(1200, 900)
        self.setAcceptDrops(True) # ENABLE DRAG AND DROP
        
        self.set_app_icon() # Try to load icon from file

        # Central Widget is now a TabWidget
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setDocumentMode(True) # Makes it look like a browser
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self.tab_switched)
        self.setCentralWidget(self.tabs)

        self.create_menu()
        self.create_toolbar()
        self.create_shortcuts()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            file_path = url.toLocalFile()
            if file_path.lower().endswith('.pdf') and os.path.exists(file_path):
                try:
                    doc = fitz.open(file_path)
                    self.create_tab(doc, file_path)
                except Exception as e:
                    print(f"Error opening dropped file: {e}")

    def set_app_icon(self):
        # 1. Look for external icon file
        icon_path = None
        for name in ["app_icon.ico", "app_icon.png", "icon.ico", "icon.png"]:
            if os.path.exists(name):
                icon_path = name
                break
        
        if icon_path:
            # Use found file
            icon = QIcon(icon_path)
            self.setWindowIcon(icon)
            QApplication.setWindowIcon(icon)
        else:
            # Fallback to generated icon
            size = 64
            pixmap = QPixmap(size, size)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            try:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setBrush(QColor("#D9381E")) 
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(0, 0, size, size, 12, 12)
                painter.setPen(QColor("white"))
                font = QFont("Arial", 22, QFont.Weight.Bold)
                painter.setFont(font)
                painter.drawText(QRect(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, "PDF")
            finally:
                painter.end()
            icon = QIcon(pixmap)
            self.setWindowIcon(icon)
            QApplication.setWindowIcon(icon)

    def create_menu(self):
        menubar = self.menuBar()
        file_menu = menubar.addMenu("File")
        
        act_open = QAction("Open...", self)
        act_open.setShortcut("Ctrl+O")
        act_open.triggered.connect(self.open_pdf)
        file_menu.addAction(act_open)

        act_url = QAction("Open from URL...", self)
        act_url.setShortcut("Ctrl+U")
        act_url.triggered.connect(self.open_url)
        file_menu.addAction(act_url)

        act_save = QAction("Save", self)
        act_save.setShortcut("Ctrl+S")
        act_save.triggered.connect(self.save_overwrite)
        file_menu.addAction(act_save)

        act_save_copy = QAction("Save Copy...", self)
        act_save_copy.setShortcut("Ctrl+Shift+S")
        act_save_copy.triggered.connect(self.save_as_new)
        file_menu.addAction(act_save_copy)

        file_menu.addSeparator()
        act_exit = QAction("Exit", self)
        act_exit.triggered.connect(self.close)
        file_menu.addAction(act_exit)

        settings_menu = menubar.addMenu("Settings")
        action_default = QAction("Set as Default App...", self)
        action_default.triggered.connect(self.open_default_settings)
        settings_menu.addAction(action_default)

    def create_toolbar(self):
        toolbar = QToolBar("Main Toolbar")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        toolbar.addAction("Open").triggered.connect(self.open_pdf)
        toolbar.addAction("Save").triggered.connect(self.save_overwrite)
        toolbar.addSeparator()
        
        self.page_input = QLineEdit()
        self.page_input.setFixedWidth(40)
        self.page_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_input.setValidator(QIntValidator(1, 9999))
        self.page_input.returnPressed.connect(self.jump_to_page_wrapper)
        
        self.page_total_lbl = QLabel(" / 0")
        
        toolbar.addWidget(self.page_input)
        toolbar.addWidget(self.page_total_lbl)
        toolbar.addSeparator()

        self.lbl_zoom = QLabel(" 100% ")
        toolbar.addAction("Zoom +").triggered.connect(lambda: self.current_tab_action("zoom_view", 0.2))
        toolbar.addAction("Zoom -").triggered.connect(lambda: self.current_tab_action("zoom_view", -0.2))
        toolbar.addWidget(self.lbl_zoom)
        toolbar.addAction("Fit").triggered.connect(lambda: self.current_tab_action("fit_width"))
        toolbar.addSeparator()
        
        toolbar.addAction("1-Page").triggered.connect(lambda: self.set_view_mode("single"))
        toolbar.addAction("2-Page").triggered.connect(lambda: self.set_view_mode("dual"))
        toolbar.addSeparator()

        self.lbl_mode = QLabel(" Mode: Browse ")
        toolbar.addWidget(self.lbl_mode)
        
        toolbar.addAction("Hand").triggered.connect(lambda: self.set_tool_wrapper("browse"))
        toolbar.addAction("Select").triggered.connect(lambda: self.set_tool_wrapper("select"))
        
        self.combo_color = QComboBox()
        self.combo_color.addItems(["Yellow", "Green", "Red", "Blue", "Orange", "Pink", "Cyan", "Purple"])
        self.combo_color.currentIndexChanged.connect(self.update_color_wrapper)
        toolbar.addWidget(self.combo_color)
        
        toolbar.addAction("Highlight").triggered.connect(lambda: self.set_tool_wrapper("highlight"))
        toolbar.addAction("Eraser").triggered.connect(lambda: self.set_tool_wrapper("erase"))

    def create_shortcuts(self):
        QShortcut(QKeySequence("Ctrl+="), self).activated.connect(lambda: self.current_tab_action("zoom_view", 0.2))
        QShortcut(QKeySequence("Ctrl+-"), self).activated.connect(lambda: self.current_tab_action("zoom_view", -0.2))
        QShortcut(QKeySequence("Ctrl+L"), self).activated.connect(self.toggle_fullscreen)
        QShortcut(QKeySequence("Esc"), self).activated.connect(self.exit_fullscreen)

    def open_default_settings(self):
        QMessageBox.information(self, "Default App", "Opening Windows Settings...")
        QDesktopServices.openUrl(QUrl("ms-settings:defaultapps"))

    # --- TAB LOGIC ---
    def open_pdf(self):
        # CHANGE: Use getOpenFileNames (plural) to select multiple files
        paths, _ = QFileDialog.getOpenFileNames(self, "Open PDF", "", "PDF Files (*.pdf)")
        if not paths: return
        for path in paths:
            try:
                doc = fitz.open(path)
                self.create_tab(doc, path)
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Could not open {os.path.basename(path)}: {e}")

    def open_url(self):
        if not HAS_REQUESTS:
            QMessageBox.warning(self, "Missing Library", "Pip install requests needed.")
            return
        url, ok = QInputDialog.getText(self, "Open from URL", "Enter PDF Link:")
        if ok and url:
            try:
                QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
                response = requests.get(url, timeout=10)
                response.raise_for_status()
                doc = fitz.open(stream=response.content, filetype="pdf")
                self.create_tab(doc, f"Web: {url[:20]}...")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Download failed: {e}")
            finally:
                QApplication.restoreOverrideCursor()

    def create_tab(self, doc, title):
        tab = PDFTab()
        tab.load_document(doc, title)
        
        # Connect signals
        tab.zoom_changed.connect(self.update_zoom_label)
        tab.page_changed.connect(self.update_page_label)
        
        # Add to TabWidget
        index = self.tabs.addTab(tab, os.path.basename(title) if os.path.exists(title) else title)
        self.tabs.setCurrentIndex(index)
        
        # Initialize UI for this tab
        self.update_zoom_label(tab.scale)
        self.update_page_label(f"1 / {len(doc)}")

    def close_tab(self, index):
        widget = self.tabs.widget(index)
        if widget:
            widget.deleteLater()
            self.tabs.removeTab(index)

    def tab_switched(self, index):
        # Update Toolbar to match new tab
        tab = self.tabs.widget(index)
        if isinstance(tab, PDFTab):
            self.update_zoom_label(tab.scale)
            # Trigger page check to update count
            tab.check_current_page()
            self.lbl_mode.setText(f" Mode: {tab.tool_mode.upper()} ")
        else:
            self.lbl_zoom.setText(" - ")
            self.page_total_lbl.setText(" / 0")

    def current_tab_action(self, method_name, *args):
        tab = self.tabs.currentWidget()
        if isinstance(tab, PDFTab):
            getattr(tab, method_name)(*args)

    # --- WRAPPERS FOR TOOLBAR ---
    def update_zoom_label(self, scale):
        self.lbl_zoom.setText(f" {int(scale * 100)}% ")

    def update_page_label(self, text):
        parts = text.split('/')
        if len(parts) == 2:
            if not self.page_input.hasFocus():
                self.page_input.setText(parts[0].strip())
            self.page_total_lbl.setText(f" / {parts[1].strip()}")

    def jump_to_page_wrapper(self):
        tab = self.tabs.currentWidget()
        if isinstance(tab, PDFTab):
            try:
                p = int(self.page_input.text())
                tab.jump_to_page(p)
            except: pass

    def set_tool_wrapper(self, mode):
        tab = self.tabs.currentWidget()
        if isinstance(tab, PDFTab):
            tab.set_tool(mode)
            self.lbl_mode.setText(f" Mode: {mode.upper()} ")
            if mode == "highlight":
                self.update_color_wrapper()

    def update_color_wrapper(self):
        color_map = {"Yellow": (1, 1, 0), "Green": (0, 1, 0), "Red": (1, 0, 0), 
                     "Blue": (0, 0, 1), "Orange": (1, 0.5, 0), "Pink": (1, 0.4, 0.7), 
                     "Cyan": (0, 1, 1), "Purple": (0.5, 0, 0.5)}
        rgb = color_map.get(self.combo_color.currentText(), (1, 1, 0))
        
        tab = self.tabs.currentWidget()
        if isinstance(tab, PDFTab):
            tab.highlight_color = rgb
            # If highlighting tool active, re-set it to update internal state
            if tab.tool_mode == "highlight":
                tab.set_tool("highlight")

    def set_view_mode(self, mode):
        tab = self.tabs.currentWidget()
        if isinstance(tab, PDFTab):
            tab.view_mode = mode
            tab.render_pages()

    def save_overwrite(self):
        tab = self.tabs.currentWidget()
        if isinstance(tab, PDFTab):
            # CRITICAL FIX: Ensure current_path is a real file before overwrite
            if not tab.current_path or not os.path.isfile(tab.current_path):
                self.save_as_new()
                return
            try:
                tab.doc.saveIncr()
                QMessageBox.information(self, "Saved", "Saved to original file.")
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))

    def save_as_new(self):
        tab = self.tabs.currentWidget()
        if isinstance(tab, PDFTab):
            path, _ = QFileDialog.getSaveFileName(self, "Save Copy", "", "PDF Files (*.pdf)")
            if path:
                try:
                    tab.doc.save(path, garbage=4, deflate=True)
                    QMessageBox.information(self, "Saved", "Copy saved.")
                    if not tab.current_path:
                        tab.current_path = path
                        self.tabs.setTabText(self.tabs.currentIndex(), os.path.basename(path))
                except Exception as e:
                    QMessageBox.critical(self, "Error", str(e))

    def toggle_fullscreen(self):
        if self.isFullScreen(): self.showNormal()
        else: self.showFullScreen()
    def exit_fullscreen(self):
        if self.isFullScreen(): self.showNormal()

if __name__ == "__main__":
    try:
        app = QApplication(sys.argv)
        app.setStyle("Fusion") 
        viewer = ProPDFViewer()
        viewer.show()

        # Handle "Open With..." from Windows Explorer AND multiple files
        if len(sys.argv) > 1:
            for file_path in sys.argv[1:]:
                if os.path.exists(file_path) and file_path.lower().endswith(".pdf"):
                    try:
                        doc = fitz.open(file_path)
                        viewer.create_tab(doc, file_path)
                    except Exception as e:
                        print(f"Startup File Error: {e}")

        sys.exit(app.exec())
    except Exception as e:
        with open("crash_log.txt", "w") as f:
            f.write(str(e))
        sys.exit(1)