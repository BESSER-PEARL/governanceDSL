import warnings
from utils.exceptions import (
    InvalidVotesException, 
    InsufficientPhasesException, 
    UndefinedRuleException, 
    UndefinedDeadlineException, 
    UnsupportedRuleTypeException,
    UndefinedParticipantException
)
from datetime import timedelta
from besser.BUML.metamodel.structural import (
    StringType, IntegerType, FloatType, TimeDeltaType
)
from metamodel.governance import (
    Policy, Project, Activity, Task, Role, Individual, Deadline, Rule
)
from .govdslParser import govdslParser
from .govdslListener import govdslListener


class PolicyCreationListener(govdslListener):
    """
       This listener class generates a governance object model from a parsed DSL file.
    """
    def __init__(self):
        super().__init__()
        self.__policy = None
        self.__project = None
        self.__project_activities = {}
        self.__scopes = {}
        self.__participants = {}
        self.__conditions = {}
        self.__rules = {}

    
    def get_policy(self):
        """Policy: Retrieves the Policy instance."""
        return self.__policy
    
    def find_descendant_nodes_by_type(self, node, target_type):
        """
        Recursively finds and returns all descendant nodes of a specified type.

        Args:
            node: The node to search for descendants. This can be any node in the parse tree.
            target_type: The type of node to match against. This should be a class type that 
                        the nodes are expected to be instances of.

        Returns:
            A list of nodes that are instances of the specified target type. 
            If no matching nodes are found, an empty list is returned.
        """
        matching_nodes = []

        if isinstance(node, target_type):
            matching_nodes.append(node)

        for i in range(node.getChildCount()):
            child = node.getChild(i)
            matching_nodes.extend(self.find_descendant_nodes_by_type(child, target_type))

        return matching_nodes
    
    def deadline_to_timedelta(self, amount:int, time_unit:str) -> timedelta:
        """
        Converts a deadline to a timedelta object.

        Args:
            amount: The amount of time to convert.
            time_unit: The unit of time to convert (e.g., days, weeks, etc.).

        Returns:
            A timedelta representation from datetime library.
        """
        if amount < 0:
            raise InvalidVotesException(amount)
        match  time_unit:
            case "days":
                return timedelta(days=amount)
            case "weeks":
                return timedelta(weeks=amount)
            case "months":
                return timedelta(weeks=amount*4) # Timedelta do not support above weeks (months, years). dateutil.relativedelta.relativedelta could be also used
            case "years":
                return timedelta(weeks=amount*52) # Approximation
            case _:
                warnings.warn(f"Unsupported time unit: {time_unit}. Defaulting to days.")
                return timedelta(days=amount)
    
    def enterActivity(self, ctx:govdslParser.ActivityContext):

        tasks = self.find_descendant_nodes_by_type(node=ctx,
                                                target_type=govdslParser.TaskContext)
        
        activity_tasks = set()
        for t in tasks:
            task_name = t.ID().getText()
            task = Task(name=task_name)
            activity_tasks.add(task)

        activity_name = ctx.ID().getText()
        activity = Activity(name=activity_name, tasks=activity_tasks)
        self.__project_activities[activity_name] = activity
    
    def enterRoles(self, ctx:govdslParser.RolesContext): 

        roles = self.find_descendant_nodes_by_type(node=ctx,
                                                target_type=govdslParser.ParticipantIDContext)
        for r in roles:
            role = Role(name=r.ID().getText())
            self.__participants[r.ID().getText()] = role # WARNING: This might generate conflict if there is a role with the same name as a individual

    def enterIndividuals(self, ctx:govdslParser.IndividualsContext):
        
        individuals = self.find_descendant_nodes_by_type(node=ctx,
                                                target_type=govdslParser.ParticipantIDContext)
        for i in individuals:
            individual = Individual(name=i.ID().getText())
            self.__participants[i.ID().getText()] = individual

    def enterDeadline(self, ctx:govdslParser.DeadlineContext):

        deadline_time = self.deadline_to_timedelta(amount=int(ctx.SIGNED_INT().getText()), time_unit=ctx.timeUnit().getText())
        deadline = Deadline(name=ctx.deadlineID().ID().getText(), ts=deadline_time)
        self.__conditions[ctx.deadlineID().ID().getText()] = deadline

    def enterRule(self, ctx:govdslParser.RuleContext):
        
        rule_name = ctx.ruleID().ID().getText()
        rule_type = ctx.ruleType().getText()
        # TODO: This goes to PolicyPhased
        # if rule_type == "Phased":
        #     phases_id = self.find_descendant_nodes_by_type(node=ctx.ruleContent().phases(),
        #                                     target_type=govdslParser.RuleIDContext)
        #     phases = set()
        #     for p_id in phases_id:
        #         phase_rule_name = p_id.ID().getText()
        #         if phase_rule_name not in self.__rules:
        #             raise UndefinedRuleException(phase_rule_name)
        #         phase_rule = self.__rules[phase_rule_name]
        #         phases.add(phase_rule)
        #     rule = Phased(name=rule_name, phases=phases)
        #     self.__rules[rule_name] = rule
        # else:
            # Create base Rule instance with common attributes
        deadline_name = ctx.ruleContent().deadlineID().ID().getText()
        applied_to = ctx.ruleContent().appliedTo().ID() # TODO: Check type? CollaborationType[r.ruleContent().appliedTo().collaborationID().getText().replace(" ", "_").upper()]
        try:
            people = {self.__participants[participant.ID().getText()] for participant in self.find_descendant_nodes_by_type(node=ctx.ruleContent(), target_type=govdslParser.ParticipantIDContext)}
        except KeyError as e:
            raise UndefinedParticipantException(e.args[0]) from e

        try:
            deadline = self.__conditions[deadline_name]
        except KeyError as e:
            raise UndefinedDeadlineException(deadline_name) from e

        # stage = Stage(r.ruleContent().stage().stageID().getText().replace(" ", "_").upper()) TODO: For now we do not populate attributes
        
        base_rule = Rule(name=rule_name, applied_to=applied_to, stage=None,
                        query_filter="", deadline=deadline, people=people) # TODO: Manage query filter

        # Transform base rule into specific rule type
        match r.ruleType().getText():
            case "Majority":
                min_votes = int(r.ruleContent().minVotes().SIGNED_INT().getText())
                if min_votes < 0:
                    raise InvalidVotesException(min_votes)
                range_type = RangeType(r.ruleContent().rangeType().rangeID().getText().replace(" ", "_").upper())
                rule = Majority.from_rule(base_rule, min_votes=min_votes, range_type=range_type)
            case "Ratio":
                min_votes = int(r.ruleContent().minVotes().SIGNED_INT().getText())
                if min_votes < 0:
                    raise InvalidVotesException(min_votes)
                range_type = RangeType(r.ruleContent().rangeType().rangeID().getText().replace(" ", "_").upper())
                ratio = float(r.ruleContent().ratio().FLOAT().getText())
                rule = RatioMajority.from_rule(base_rule, min_votes=min_votes, range_type=range_type, ratio=ratio)
            case "LeaderDriven":
                default_name = r.ruleContent().default().ruleID().ID().getText()
                if default_name not in self.__rules:
                    raise UndefinedRuleException(default_name)
                default_rule = self.__rules[default_name]
                rule = LeaderDriven.from_rule(base_rule, default=default_rule)
            case _: 
                raise UnsupportedRuleTypeException(r.ruleType().getText())
        self.__rules[rule_name] = rule

    def exitProject(self, ctx:govdslParser.ProjectContext):
        
        self.__project.activities = set(self.__project_activities.values())
