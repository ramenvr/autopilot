// `rc` (docs/protocol.md): Remote Control compatible calls on the running game. Call a function, read or write
// a property, or describe an object, with the same bodies and results as Unreal's Remote Control HTTP API.

#include "PlaytestOps.h"

#include "PlaytestJson.h"
#include "UObject/StructOnScope.h"
#include "UObject/UnrealType.h"

namespace PlaytestOps
{
namespace
{
	/** Why a change to this object is refused, "" when it isn't: the editor's Play settings (the net mode, the number
	 *  of clients...) are the user's own. A changed net mode outlives the run, and a game made for one may not start in
	 *  another (Play In Editor never begins, and every later run finds no game). */
	FString Refused(const UObject* Object)
	{
		for (const UClass* Class = Object ? Object->GetClass() : nullptr; Class; Class = Class->GetSuperClass())
		{
			if (Class->GetFName() == TEXT("LevelEditorPlaySettings"))
			{
				return TEXT("The editor's Play settings (LevelEditorPlaySettings: net mode, clients...) are the user's and "
					"aren't changed by a run: the game is played as it is set up. If a setting stands in the way, say so in "
					"the report");
			}
		}
		return FString();
	}

	/** An argument the function takes from a caller: not its return value, nor an out parameter only written. */
	bool IsInput(const FProperty* Param)
	{
		return !Param->HasAnyPropertyFlags(CPF_ReturnParm)
			&& (!Param->HasAnyPropertyFlags(CPF_OutParm) || Param->HasAnyPropertyFlags(CPF_ReferenceParm | CPF_ConstParm));
	}

	/** Why a call's arguments are refused for names the function has no parameter for, "" when they aren't: such a
	 *  value would go unread and the call be made without it, while the caller believes it was passed. Names match
	 *  as the arguments are found (case-insensitive). The words are Unity's and Godot's: "unknown argument(s) X; it
	 *  takes A, B". */
	FString UnknownArguments(UFunction* Function, const TSharedPtr<FJsonObject>& Params)
	{
		if (!Params.IsValid())
		{
			return FString();
		}
		TArray<FString> Unknown;
		for (const TPair<FString, TSharedPtr<FJsonValue>>& Pair : Params->Values)
		{
			bool bKnown = false;
			for (TFieldIterator<FProperty> It(Function); It && It->HasAnyPropertyFlags(CPF_Parm); ++It)
			{
				if (!It->HasAnyPropertyFlags(CPF_ReturnParm) && It->GetName().Equals(Pair.Key, ESearchCase::IgnoreCase))
				{
					bKnown = true;
					break;
				}
			}
			if (!bKnown)
			{
				Unknown.Add(Pair.Key);
			}
		}
		if (Unknown.Num() == 0)
		{
			return FString();
		}
		TArray<FString> Takes;
		for (TFieldIterator<FProperty> It(Function); It && It->HasAnyPropertyFlags(CPF_Parm); ++It)
		{
			if (IsInput(*It))
			{
				Takes.Add(It->GetName());
			}
		}
		return FString::Printf(TEXT("unknown argument(s) %s; it takes %s"), *FString::Join(Unknown, TEXT(", ")),
			Takes.Num() > 0 ? *FString::Join(Takes, TEXT(", ")) : TEXT("no arguments"));
	}

	/** World: the world a function's world context gets when the call doesn't give one (the request's player's). */
	void Call(UPlaytestSubsystem& Bridge, int64 Id, const TSharedPtr<FJsonObject>& Body, UWorld* World)
	{
		const FString Path = StringField(Body, TEXT("objectPath"));
		UObject* Object = PlaytestJson::ResolveObject(Path);
		if (!Object)
		{
			Bridge.RespondError(Id, Missing(Path));
			return;
		}
		const FString Name = StringField(Body, TEXT("functionName"));
		const FString Why = Refused(Object);
		if (!Why.IsEmpty())
		{
			Bridge.RespondError(Id, Why);
			return;
		}
		UFunction* Function = Object->FindFunction(FName(*Name));
		if (!Function)
		{
			Bridge.RespondError(Id, FString::Printf(TEXT("Function: %s does not exist on object: %s"), *Name, *Path));
			return;
		}
		const TSharedPtr<FJsonObject> Params = ObjectField(Body, TEXT("parameters"));
		const FString Unknown = UnknownArguments(Function, Params);
		if (!Unknown.IsEmpty())
		{
			Bridge.RespondError(Id, FString::Printf(TEXT("%s on %s: %s"), *Name, *Path, *Unknown));
			return;
		}
		FStructOnScope Scope(Function);
		uint8* Parms = Scope.GetStructMemory();
#if WITH_METADATA
		const FString WorldContext = Function->GetMetaData(TEXT("WorldContext"));
#else
		const FString WorldContext;
#endif
		for (TFieldIterator<FProperty> It(Function); It && It->HasAnyPropertyFlags(CPF_Parm); ++It)
		{
			FProperty* Param = *It;
			if (Param->HasAnyPropertyFlags(CPF_ReturnParm))
			{
				continue;
			}
			void* ValuePtr = Param->ContainerPtrToValuePtr<void>(Parms);
			if (TSharedPtr<FJsonValue> Value = FindField(Params, Param->GetName()))
			{
				FString Error;
				if (!PlaytestJson::JsonToProperty(Param, ValuePtr, Value, Error))
				{
					Bridge.RespondError(Id, FString::Printf(TEXT("%s on %s: %s"), *Name, *Path, *Error));
					return;
				}
				continue;
			}
			if (!WorldContext.IsEmpty() && Param->GetName() == WorldContext)
			{
				if (FObjectPropertyBase* ObjParam = CastField<FObjectPropertyBase>(Param))
				{
					ObjParam->SetObjectPropertyValue(ValuePtr, World);
				}
				continue;
			}
#if WITH_METADATA
			const FString Default = Function->GetMetaData(*FString::Printf(TEXT("CPP_Default_%s"), *Param->GetName()));
			if (!Default.IsEmpty())
			{
				Param->ImportText_Direct(*Default, ValuePtr, nullptr, PPF_None, GLog);
			}
#endif
		}
		// A call that may change the game (not a read: pure, const, Get..., Find...) is the agent's own: a teleport it makes
		// isn't the game's
		const bool bRead = Function->HasAnyFunctionFlags(FUNC_BlueprintPure | FUNC_Const)
			|| Name.StartsWith(TEXT("Get")) || Name.StartsWith(TEXT("K2_Get")) || Name.StartsWith(TEXT("Find"))
			|| Name.StartsWith(TEXT("Is")) || Name.StartsWith(TEXT("Has")) || Name.StartsWith(TEXT("Can"));
		if (!bRead)
		{
			Bridge.GetDetectors().NoteAgentMove();
		}
		Object->ProcessEvent(Function, Parms);
		TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
		for (TFieldIterator<FProperty> It(Function); It && It->HasAnyPropertyFlags(CPF_Parm); ++It)
		{
			FProperty* Param = *It;
			const bool bOut = Param->HasAnyPropertyFlags(CPF_ReturnParm)
				|| (Param->HasAnyPropertyFlags(CPF_OutParm) && !Param->HasAnyPropertyFlags(CPF_ConstParm));
			if (!bOut)
			{
				continue;
			}
			TSharedPtr<FJsonValue> Value = PlaytestJson::PropertyInContainerToJson(Param, Parms);
			if (Value.IsValid())
			{
				Result->SetField(Param->GetName(), Value);
			}
		}
		Bridge.Respond(Id, Obj(Result));
	}

	void Property(UPlaytestSubsystem& Bridge, int64 Id, const TSharedPtr<FJsonObject>& Body)
	{
		const FString Path = StringField(Body, TEXT("objectPath"));
		UObject* Object = PlaytestJson::ResolveObject(Path);
		if (!Object)
		{
			Bridge.RespondError(Id, Missing(Path));
			return;
		}
		const FString Name = StringField(Body, TEXT("propertyName"));
		const TSharedPtr<FJsonObject> NewValue = ObjectField(Body, TEXT("propertyValue"));
		const bool bWrite = NewValue.IsValid() || StringField(Body, TEXT("access")).StartsWith(TEXT("WRITE"));
		if (Name.IsEmpty())
		{
			if (bWrite)
			{
				Bridge.RespondError(Id, TEXT("A write needs propertyName"));
				return;
			}
			Bridge.Respond(Id, Obj(PlaytestJson::StructToJson(Object->GetClass(), Object)));
			return;
		}
		FProperty* Prop = PlaytestJson::FindProperty(Object->GetClass(), Name);
		if (!Prop || (!bWrite && PlaytestJson::IsHidden(Prop)))
		{
			Bridge.RespondError(Id, FString::Printf(TEXT("Property %s does not exist on object %s"), *Name, *Path));
			return;
		}
		if (bWrite)
		{
			const FString Why = Refused(Object);
			if (!Why.IsEmpty())
			{
				Bridge.RespondError(Id, Why);
				return;
			}
			TSharedPtr<FJsonValue> Value = FindField(NewValue, Name);
			if (!Value.IsValid() && NewValue.IsValid() && NewValue->Values.Num() == 1)
			{
				Value = NewValue->Values.CreateConstIterator().Value();
			}
			FString Error;
			if (!Value.IsValid())
			{
				Bridge.RespondError(Id, FString::Printf(TEXT("No value given for %s"), *Name));
				return;
			}
			if (Prop->ArrayDim != 1)
			{
				Bridge.RespondError(Id, FString::Printf(TEXT("%s is a fixed-size array and can't be written"), *Name));
				return;
			}
			if (!PlaytestJson::JsonToProperty(Prop, Prop->ContainerPtrToValuePtr<void>(Object), Value, Error))
			{
				Bridge.RespondError(Id, Error);
				return;
			}
			Bridge.GetDetectors().NoteAgentMove();
			Bridge.Respond(Id, Obj(MakeShared<FJsonObject>()));
			return;
		}
		TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
		TSharedPtr<FJsonValue> Value = PlaytestJson::PropertyInContainerToJson(Prop, Object);
		Result->SetField(Name, Value.IsValid() ? Value : MakeShared<FJsonValueNull>());
		Bridge.Respond(Id, Obj(Result));
	}

	FString Tooltip(const FField* Field)
	{
#if WITH_METADATA
		return Field->GetMetaData(TEXT("ToolTip"));
#else
		return FString();
#endif
	}

	void Describe(UPlaytestSubsystem& Bridge, int64 Id, const TSharedPtr<FJsonObject>& Body)
	{
		const FString Path = StringField(Body, TEXT("objectPath"));
		UObject* Object = PlaytestJson::ResolveObject(Path);
		if (!Object)
		{
			Bridge.RespondError(Id, Missing(Path));
			return;
		}
		UClass* Class = Object->GetClass();
		TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
		Result->SetStringField(TEXT("Name"), Object->GetName());
		Result->SetStringField(TEXT("Class"), Class->GetPathName());
		TArray<TSharedPtr<FJsonValue>> Properties;
		for (TFieldIterator<FProperty> It(Class); It; ++It)
		{
			if (PlaytestJson::IsHidden(*It))
			{
				continue;
			}
			TSharedRef<FJsonObject> P = MakeShared<FJsonObject>();
			P->SetStringField(TEXT("Name"), It->GetAuthoredName());
			P->SetStringField(TEXT("Description"), Tooltip(*It));
			P->SetStringField(TEXT("Type"), It->GetCPPType());
			Properties.Add(Obj(P));
		}
		Result->SetArrayField(TEXT("Properties"), Properties);
		TArray<TSharedPtr<FJsonValue>> Functions;
		for (TFieldIterator<UFunction> It(Class, EFieldIteratorFlags::IncludeSuper); It; ++It)
		{
			UFunction* Function = *It;
			if (!Function->HasAnyFunctionFlags(FUNC_BlueprintCallable | FUNC_BlueprintPure))
			{
				continue;
			}
			TSharedRef<FJsonObject> F = MakeShared<FJsonObject>();
			F->SetStringField(TEXT("Name"), Function->GetName());
#if WITH_METADATA
			F->SetStringField(TEXT("Description"), Function->GetMetaData(TEXT("ToolTip")));
#else
			F->SetStringField(TEXT("Description"), FString());
#endif
			TArray<TSharedPtr<FJsonValue>> Arguments;
			for (TFieldIterator<FProperty> P(Function); P && P->HasAnyPropertyFlags(CPF_Parm); ++P)
			{
				if (!IsInput(*P))
				{
					continue;
				}
				TSharedRef<FJsonObject> A = MakeShared<FJsonObject>();
				A->SetStringField(TEXT("Name"), P->GetName());
				A->SetStringField(TEXT("Type"), P->GetCPPType());
				Arguments.Add(Obj(A));
			}
			F->SetArrayField(TEXT("Arguments"), Arguments);
			Functions.Add(Obj(F));
		}
		Result->SetArrayField(TEXT("Functions"), Functions);
		Bridge.Respond(Id, Obj(Result));
	}
}

	void Rc(UPlaytestSubsystem& Bridge, int64 Id, const TSharedPtr<FJsonObject>& Args)
	{
		const FString Verb = StringField(Args, TEXT("verb"));
		const TSharedPtr<FJsonObject> Body = ObjectField(Args, TEXT("body"));
		if (!Body.IsValid())
		{
			Bridge.RespondError(Id, TEXT("rc needs a body"));
		}
		else if (Verb == TEXT("call"))
		{
			Call(Bridge, Id, Body, PlayerWorld(Bridge, Args));
		}
		else if (Verb == TEXT("property"))
		{
			Property(Bridge, Id, Body);
		}
		else if (Verb == TEXT("describe"))
		{
			Describe(Bridge, Id, Body);
		}
		else
		{
			Bridge.RespondError(Id, FString::Printf(TEXT("unknown rc verb %s"), *Verb));
		}
	}
}
