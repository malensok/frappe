# imports - standard imports
import json

# imports - module imports
from   frappe.model.document import Document
from   frappe import _
import frappe

# imports - frappe module imports
from frappe.core.doctype.version.version import get_diff
from frappe.chat.doctype.chat_message	 import chat_message
from frappe.chat.util import (
	safe_json_loads,
	dictify,
	listify,
	squashify,
	assign_if_empty
)

session = frappe.session

def is_direct(owner, other, bidirectional = False):
	def get_room(owner, other):
		room = frappe.get_all('Chat Room', filters = [
			['Chat Room', 	   'type' , 'in', ('Direct', 'Visitor')],
			['Chat Room', 	   'owner', '=' , owner],
			['Chat Room User', 'user' , '=' , other]
		], distinct = True)

		return room

	exists = len(get_room(owner, other)) == 1
	if bidirectional:
		exists = exists or len(get_room(other, owner)) == 1
	
	return exists

def get_chat_room_user_set(users, filter_ = None):
	seen, uset = set(), list()

	for u in users:
		if filter_(u) and u.user not in seen:
			uset.append(u)
			seen.add(u.user)

	return uset

class ChatRoom(Document):
	def validate(self):
		if self.is_new():
			users = get_chat_room_user_set(self.users, filter_ = lambda u: u.user != session.user)
			self.update(dict(
				users = users
			))

		if self.type in ("Direct", "Visitor"):
			if len(self.users) != 1:
				frappe.throw(_('{type} room must have atmost one user.'.format(type = self.type)))

			other = squashify(self.users)

			if self.is_new():
				if is_direct(self.owner, other.user, bidirectional = True):
					frappe.throw(_('Direct room with {other} already exists.'.format(
						other = other.user
					)))

		if self.type == "Group" and not self.room_name:
			frappe.throw(_('Group name cannot be empty.'))
	
	def before_save(self):
		if not self.is_new():
			self.get_doc_before_save()

	def on_update(self):
		if not self.is_new():
			before = self.get_doc_before_save()
			after  = self

			diff   = dictify(get_diff(before, after))
			if diff:
				update = { }
				for changed in diff.changed:
					field, old, new = changed
					
					if field == 'last_message':
						new = chat_message.get(new)

					update.update({ field: new })
				
				if diff.added or diff.removed:
					update.update(dict(users = [u.user for u in self.users]))

				update = dict(room = self.name, data = update)

				frappe.publish_realtime('frappe.chat.room:update', update, room = self.name, after_commit = True)

def authenticate(user):
	if user != session.user:
		frappe.throw(_("Sorry, you're not authorized."))

@frappe.whitelist()
def get(user, rooms = None, fields = None, filters = None):
	# There is this horrible bug out here.
	# Looks like if frappe.call sends optional arguments (not in right order), the argument turns to an empty string.
	# I'm not even going to think searching for it.
	# Hence, the hack was assign_if_empty (previous assign_if_none)
	# - Achilles Rasquinha achilles@frappe.io
	authenticate(user)

	rooms, fields, filters = safe_json_loads(rooms, fields, filters)

	rooms   = listify(assign_if_empty(rooms,  [ ]))
	fields  = listify(assign_if_empty(fields, [ ]))

	const   = [ ] # constraints
	if rooms:
		const.append(['Chat Room', 'name', 'in', rooms])
	if filters:
		if isinstance(filters[0], list):
			const = const + filters
		else:
			const.append(filters)

	default = ['name', 'type', 'room_name', 'creation', 'owner', 'avatar']
	handle  = ['users', 'last_message']
	
	param   = [f for f in fields if f not in handle]

	rooms   = frappe.get_all('Chat Room',
		or_filters = [
			['Chat Room', 	   'owner', '=', user],
			['Chat Room User', 'user',  '=', user]
		],
		filters  = const,
		fields   = param + ['name'] if param else default,
		distinct = True
	)

	if not fields or 'users' in fields:
		for i, r in enumerate(rooms):
			droom = frappe.get_doc('Chat Room', r.name)
			rooms[i]['users'] = [ ]

			for duser in droom.users:
				rooms[i]['users'].append(duser.user)

	if not fields or 'last_message' in fields:
		for i, r in enumerate(rooms):
			droom = frappe.get_doc('Chat Room', r.name)
			if droom.last_message:
				rooms[i]['last_message'] = chat_message.get(droom.last_message)
			else:
				rooms[i]['last_message'] = None

	rooms = squashify(dictify(rooms))
	
	return rooms

@frappe.whitelist()
def create(kind, owner, users = None, name = None):
	authenticate(owner)

	users = safe_json_loads(users)

	room  = frappe.new_doc('Chat Room')
	room.type 	   = kind
	room.owner	   = owner
	room.room_name = name

	dusers     	   = [ ]

	if users:
		users  = listify(users)
		for user in users:
			duser 	   = frappe.new_doc('Chat Room User')
			duser.user = user
			dusers.append(duser)
	
	room.users = dusers
	room.save(ignore_permissions = True)

	room  = get(owner, rooms = room.name)
	users = [room.owner] + [u for u in room.users]

	for u in users:
		frappe.publish_realtime('frappe.chat.room:create', room, user = u, after_commit = True)

	return room

@frappe.whitelist()
def history(room, user = None, pagination = 20):
	mess = chat_message.get_messages(room, pagination = pagination)

	mess = squashify(mess)
	
	return dictify(mess)