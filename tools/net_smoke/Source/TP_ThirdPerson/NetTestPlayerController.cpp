#include "NetTestPlayerController.h"

#include "EngineUtils.h"
#include "EnhancedInputComponent.h"
#include "EnhancedInputSubsystems.h"
#include "Engine/LocalPlayer.h"
#include "GameFramework/Pawn.h"
#include "InputAction.h"
#include "InputMappingContext.h"
#include "Net/UnrealNetwork.h"
#include "NetDummy.h"

void ANetTestPlayerController::SetupInputComponent()
{
	Super::SetupInputComponent();
	// (made at run time: an Enhanced Input action and its mapping, F, beside the template's own)
	AttackAction = NewObject<UInputAction>(this, TEXT("IA_Attack"));
	AttackContext = NewObject<UInputMappingContext>(this, TEXT("IMC_Attack"));
	AttackContext->MapKey(AttackAction, EKeys::F);
	if (UEnhancedInputComponent* Input = Cast<UEnhancedInputComponent>(InputComponent))
	{
		Input->BindAction(AttackAction, ETriggerEvent::Started, this, &ANetTestPlayerController::Attack);
	}
	if (UEnhancedInputLocalPlayerSubsystem* Subsystem = ULocalPlayer::GetSubsystem<UEnhancedInputLocalPlayerSubsystem>(GetLocalPlayer()))
	{
		Subsystem->AddMappingContext(AttackContext, 1);
	}
}

void ANetTestPlayerController::Attack()
{
	ServerAttack();
}

void ANetTestPlayerController::ServerAttack_Implementation()
{
	APawn* Me = GetPawn();
	if (!Me)
	{
		return;
	}
	++Hits;
	for (TActorIterator<ANetDummy> It(GetWorld()); It; ++It)
	{
		const FVector To = It->GetActorLocation() - Me->GetActorLocation();
		if (To.Size2D() < 250.0 && FVector::DotProduct(To.GetSafeNormal2D(), Me->GetActorForwardVector()) > 0.3)
		{
			It->TakeHit(25.f);
		}
	}
}

void ANetTestPlayerController::GetLifetimeReplicatedProps(TArray<FLifetimeProperty>& OutLifetimeProps) const
{
	Super::GetLifetimeReplicatedProps(OutLifetimeProps);
	DOREPLIFETIME(ANetTestPlayerController, Hits);
}
