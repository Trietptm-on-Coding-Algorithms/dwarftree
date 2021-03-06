#!/usr/bin/python
from gi.repository import Gtk
from gi.repository import GLib
from gi.repository import Gio
import dwarfmodeltest
from elftools.elf.elffile import ELFFile
from dwarfmodel import DwarfModelBuilder, ChildrenGroup

import threading
import argparse
import signal
import sys
import os

class DwarfLoaderThread(threading.Thread):
    def __init__(self, window, f, verbose):
        super(DwarfLoaderThread, self).__init__()
        self.f = f
        self.window = window
        self.stop_requested = False
        self.verbose = verbose

    def request_stop(self):
        self.stop_requested = True

    def run(self):
        elf = ELFFile(self.f)

        if not elf.has_dwarf_info():
            GLib.idle_add(self.window.display_error, "This file has no DWARF info.")
            return

        di = elf.get_dwarf_info()

        builder = DwarfModelBuilder(di, self.verbose)
        total = builder.num_cus()
        n = 0

        generator = builder.build_step()
        file_elem = next(generator)
        while not file_elem:
            if self.stop_requested:
                return

            GLib.idle_add(self.window.load_progress, float(n) / total)
            n = n + 1
            file_elem = next(generator)


        #root_elem = builder.build()

        if self.stop_requested:
            return

        GLib.idle_add(self.window.done_loading, file_elem)


class DwarfUi(Gtk.Window):
    def __init__(self, file_to_open = None, verbose = False):
        super(DwarfUi, self).__init__(title = "DWARF Tree")

        self.verbose = verbose

        self.connect("delete-event", Gtk.main_quit)

        self.set_default_size(640, 480)
        self.maximize()

        box = Gtk.Box(orientation = Gtk.Orientation.VERTICAL)
        self.add(box)

        menubar, toolbar = self.build_menus(
            os.path.join(os.path.dirname(__file__), "menus.xml"))

        box.pack_start(menubar, False, False, 0)
        box.pack_start(toolbar, False, False, 0)

        self.tree = self.build_tree_view()

        tree_scrolled_win = Gtk.ScrolledWindow()
        tree_scrolled_win.add(self.tree)

        box.pack_start(tree_scrolled_win, True, True, 0)

        # Status bar stuff
        statusbarbox = Gtk.Box(orientation = Gtk.Orientation.HORIZONTAL)
        box.pack_end(statusbarbox, False, False, 0)

        self.statusbar = Gtk.Statusbar()
        self.statusbar_context_id = self.statusbar.get_context_id("some context")
        self.statusbar.push(self.statusbar_context_id, "Welcome !")

        self.loading_progress_bar = Gtk.ProgressBar()

        statusbarbox.pack_start(self.statusbar, True, True, 0)
        statusbarbox.pack_end(self.loading_progress_bar, False, False, 0)

        self.loader_thread = None

        if file_to_open:
            self.open_file(file_to_open)

    def build_menus(self, menus_xml_file):
        uimanager = self.create_ui_manager(menus_xml_file)

        action_group = Gtk.ActionGroup(name = "actions")

        # File menu
        action_filemenu = Gtk.Action(name = "FileMenu", label = "File", tooltip = None, stock_id = None)
        action_group.add_action(action_filemenu)

        action_fileopen = Gtk.Action(name = "FileOpen", label = "Open", tooltip = "Open a DWARF file", stock_id = Gtk.STOCK_OPEN)
        action_group.add_action_with_accel(action_fileopen, None)
        action_fileopen.connect("activate", self.on_menu_file_open)

        action_filequit = Gtk.Action(name = "FileQuit", label = "Quit", tooltip = None, stock_id = Gtk.STOCK_QUIT)
        action_group.add_action_with_accel(action_filequit, None)
        action_filequit.connect("activate", self.on_menu_file_quit)

        # Edit menu
        action_editmenu = Gtk.Action(name = "EditMenu", label = "Edit", tooltip = None, stock_id = None)
        action_group.add_action(action_editmenu)

        action_editfind = Gtk.Action(name = "EditFind", label = "Find", tooltip = None, stock_id = Gtk.STOCK_FIND)
        action_group.add_action(action_editfind)
        action_editfind.connect("activate", self.on_menu_edit_find)

        uimanager.insert_action_group(action_group)

        menubar = uimanager.get_widget("/MenuBar")
        toolbar = uimanager.get_widget("/ToolBar")

        return menubar, toolbar

    def create_ui_manager(self, menus_xml_file):
        uimanager = Gtk.UIManager()

        uimanager.add_ui_from_file(menus_xml_file)
        accelgroup = uimanager.get_accel_group()
        self.add_accel_group(accelgroup)

        return uimanager


    def build_tree_view(self):
        tree = Gtk.TreeView()

        tree.append_column(Gtk.TreeViewColumn("Element", Gtk.CellRendererText(), text = 0))
        tree.append_column(Gtk.TreeViewColumn("Offset",  Gtk.CellRendererText(), text = 1))
        tree.append_column(Gtk.TreeViewColumn("Type",  Gtk.CellRendererText(), text = 2))

        return tree

    def build_tree_store(self, root_element):
        store = Gtk.TreeStore(str, str, str)

        if root_element is not None:

            # Create root element
            root_iter = store.append(None, [root_element.name, "", ""])

            self.build_tree_store_rec(store, root_iter, root_element)

        return store

    def build_tree_store_rec(self, store, parent_iter, parent):
        for group_id in parent.children_groups:
            children_list = parent.children_groups[group_id]
            if group_id is not None:
                group_name = ChildrenGroup.name(group_id)
                # Add a tree element for the group
                add_to_iter = store.append(parent_iter, [group_name, "", ""])
            else:
                add_to_iter = parent_iter

            for child in children_list:
                values = self.build_element_row_values(child)
                child_iter = store.append(add_to_iter, values)

                self.build_tree_store_rec(store, child_iter, child)

    def build_element_row_values(self, elem):
        ret = []

        ret.append(elem.name)
        ret.append("0x%x" % (elem.die.offset))
        ret.append(elem.type_string if elem.type_string else "")

        return ret

    def open_file(self, filename):
        try:
            f = open(filename, 'rb')
            if self.loader_thread:
                self.loader_thread.request_stop()

            self.loader_thread = DwarfLoaderThread(self, f, self.verbose)
            self.loader_thread.start()
            self.display_status("Loading...")

        except FileNotFoundError as e:
            self.display_status("File %s not found..." % (filename))

    def on_menu_file_open(self, widget):
        dialog = Gtk.FileChooserDialog(
            title = "Choose an ELF binary",
            parent = self, action = Gtk.FileChooserAction.OPEN)
        dialog.add_button(Gtk.STOCK_OK, Gtk.ResponseType.OK)
        dialog.add_button(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL)

        resp = dialog.run()

        if resp == Gtk.ResponseType.OK:
            self.open_file(dialog.get_filename())

        dialog.destroy()

    def on_menu_edit_find(self, widget):
        print("Pressed find")

    def on_menu_file_quit(self, widget):
        Gtk.main_quit()

    def display_error(self, text):
        dialog = Gtk.MessageDialog(
            parent = self,
            text = text,
            message_type = Gtk.MessageType.ERROR)
        dialog.add_button(Gtk.STOCK_OK, Gtk.ResponseType.OK)
        dialog.run()
        dialog.destroy()

    def done_loading(self, root_elem):
        store = self.build_tree_store(root_elem)
        self.tree.set_model(store)
        self.display_status("Done loading")

    def display_status(self, text):
        self.statusbar.push(self.statusbar_context_id, text)

    def load_progress(self, fraction):
        self.loading_progress_bar.set_fraction(fraction)

def print_version():
    print("DWARF Tree version 0.00001bbb")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('elfbinary', help = 'The ELF binary to analyze', nargs = '?')
    parser.add_argument('--verbose', action = "store_true")
    parser.add_argument('--version', action = "store_true")
    args = parser.parse_args()

    if args.version:
        print_version()
        sys.exit(0)

    if args.verbose:
        print('Verbose mode enabled.')

    win = DwarfUi(args.elfbinary, verbose = args.verbose)
    win.show_all()
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    Gtk.main()


