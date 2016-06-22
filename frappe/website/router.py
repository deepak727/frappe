# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# MIT License. See license.txt

from __future__ import unicode_literals
import frappe, os

from frappe.website.utils import can_cache, delete_page_cache
from frappe.model.document import get_controller

def get_page_context(path):
	page_context = None
	if can_cache():
		page_context_cache = frappe.cache().hget("page_context", path) or {}
		page_context = page_context_cache.get(frappe.local.lang, None)

	if not page_context:
		page_context = make_page_context(path)
		if can_cache(page_context.no_cache):
			page_context_cache[frappe.local.lang] = page_context
			frappe.cache().hset("page_context", path, page_context_cache)

	return page_context

def make_page_context(path):
	context = resolve_route(path)
	if not context:
		raise frappe.DoesNotExistError

	context.doctype = context.ref_doctype
	context.title = context.page_title
	context.pathname = frappe.local.path

	return context

def resolve_route(path):
	"""Returns the page route object based on searching in pages and generators.
	The `www` folder is also a part of generator **Web Page**.

	The only exceptions are `/about` and `/contact` these will be searched in Web Pages
	first before checking the standard pages."""
	if path not in ("about", "contact"):
		context = get_page_context_from_template(path)
		if context:
			return context
		return get_page_context_from_doctype(path)
	else:
		context = get_page_context_from_doctype(path)
		if context:
			return context
		return get_page_context_from_template(path)

def get_page_context_from_template(path):
	found = filter(lambda p: p.page_name==path, get_pages())
	return found[0] if found else None

def get_page_context_from_doctype(path):
	generator_routes = get_page_context_from_doctypes()
	if path in generator_routes:
		route = generator_routes[path]
		return frappe.get_doc(route.get("doctype"), route.get("name")).get_route_context()

def clear_sitemap():
	delete_page_cache("*")

def get_page_context_from_doctypes():
	routes = frappe.cache().get_value("website_generator_routes")
	if not routes:
		routes = {}
		for app in frappe.get_installed_apps():
			for doctype in frappe.get_hooks("website_generators", app_name = app):
				condition = ""
				route_column_name = "page_name"
				controller = get_controller(doctype)
				meta = frappe.get_meta(doctype)

				if meta.get_field("parent_website_route"):
					route_column_name = """concat(ifnull(parent_website_route, ""),
						if(ifnull(parent_website_route, "")="", "", "/"), page_name)"""

				if controller.website.condition_field:
					condition ="where {0}=1".format(controller.website.condition_field)

				for r in frappe.db.sql("""select {0} as route, name, modified from `tab{1}`
						{2}""".format(route_column_name, doctype, condition), as_dict=True):
					routes[r.route] = {"doctype": doctype, "name": r.name, "modified": r.modified}

		frappe.cache().set_value("website_generator_routes", routes)

	return routes

def get_pages():
	pages = frappe.cache().get_value("_website_pages") if can_cache() else []

	if not pages:
		pages = []
		for app in frappe.get_installed_apps():
			app_path = frappe.get_app_path(app)

			# old
			path = os.path.join(app_path, "templates", "pages")
			pages += get_pages_from_path(path, app, app_path)

			# new
			path = os.path.join(app_path, "www")
			pages += get_pages_from_path(path, app, app_path)

		frappe.cache().set_value("_website_pages", pages)
	return pages

def get_pages_from_path(path, app, app_path):
	pages = []
	if os.path.exists(path):
		for basepath, folders, files in os.walk(path):
			# add missing __init__.py
			if not '__init__.py' in files:
				open(os.path.join(basepath, '__init__.py'), 'a').close()

			for fname in files:
				fname = frappe.utils.cstr(fname)
				page_name, extn = fname.rsplit(".", 1)
				if extn in ('js', 'css') and os.path.exists(os.path.join(basepath, fname + '.html')):
					# js, css is linked to html, skip
					continue

				if extn in ("html", "xml", "js", "css", "md"):
					pages.append(get_page_info(path, basepath, app, app_path, fname))
					# print frappe.as_json(pages[-1])

	return pages

def get_page_info(path, basepath, app, app_path, fname):
	'''Load page info'''
	page_name, extn = fname.rsplit(".", 1)

	# add website route
	page_info = frappe._dict()

	page_info.basename = page_name if extn in ('html', 'md') else fname
	page_info.page_or_generator = "Page"

	page_info.template = os.path.relpath(os.path.join(basepath, fname), app_path)

	if page_info.basename == 'index' and basepath != path:
		page_info.basename = ''

	page_info.name = page_info.page_name = os.path.join(os.path.relpath(basepath, path),
		page_info.basename).strip('/').strip('.').strip('/')

	page_info.controller_path = os.path.join(basepath, page_name.replace("-", "_") + ".py")

	# get the source
	page_info.source = get_source(page_info, basepath)

	# extract properties from HTML comments
	if page_info.only_content:
		load_properties(page_info)

	# controller
	controller = app + "." + os.path.relpath(page_info.controller_path,
		app_path).replace(os.path.sep, ".")[:-3]
	page_info.controller = controller

	return page_info


def get_source(page_info, basepath):
	'''Get the HTML source of the template'''
	from markdown2 import markdown
	jenv = frappe.get_jenv()
	source = jenv.loader.get_source(jenv, page_info.template)[0]
	html = ''

	if page_info.template.endswith('.md'):
		source = markdown(source)

	# if only content
	if page_info.template.endswith('.html') or page_info.template.endswith('.md'):
		if ('</body>' not in source) and ('{% block' not in source):
			page_info.only_content = True
			js, css = '', ''

			js_path = os.path.join(basepath, page_info.basename + '.js')
			if os.path.exists(js_path):
				js = unicode(open(js_path, 'r').read(), 'utf-8')

			css_path = os.path.join(basepath, page_info.basename + '.css')
			if os.path.exists(css_path):
				js = unicode(open(css_path, 'r').read(), 'utf-8')

			html = '{% extends "templates/web.html" %}'

			if css:
				html += '\n{% block style %}\n<style>\n' + css + '\n</style>\n{% endblock %}'

			html += '\n{% block page_content %}\n' + source + '\n{% endblock %}'

			if js:
				html += '\n{% block script %}<script>' + js + '\n</script>\n{% endblock %}'
		else:
			html = source

	return html

def load_properties(page_info):
	'''Load properties like no_cache, title from raw'''
	import re
	if "<!-- title:" in page_info.source:
		page_info.title = re.findall('<!-- title:([^>]*) -->', page_info.source)[0].strip()
	else:
		page_info.title = os.path.basename(page_info.name).replace('_', ' ').replace('-', ' ').title()

	if not '{% block title %}' in page_info.source:
		page_info.source += '\n{% block title %}' + page_info.title + '{% endblock %}'

	if "<!-- no-breadcrumbs -->" in page_info.source:
		page_info.no_breadcrumbs = 1

	if "<!-- no-header -->" in page_info.source:
		page_info.no_header = 1
	else:
		# every page needs a header
		# add missing header if there is no <h1> tag
		if (not '{% block header %}' in page_info.source) and (not '<h1' in page_info.source):
			page_info.source += '\n{% block header %}<h1>' + page_info.title + '</h1>{% endblock %}'

	if "<!-- no-cache -->" in page_info.source:
		page_info.no_cache = 1

def process_generators(func):
	for app in frappe.get_installed_apps():
		for doctype in frappe.get_hooks("website_generators", app_name = app):
			order_by = "name asc"
			condition_field = None
			controller = get_controller(doctype)

			if hasattr(controller, "condition_field"):
				condition_field = controller.condition_field
			if hasattr(controller, "order_by"):
				order_by = controller.order_by

			val = func(doctype, condition_field, order_by)
			if val:
				return val
